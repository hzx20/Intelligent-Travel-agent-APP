"""Text import does not write until confirmed and preserves provenance on dedupe."""
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base, get_db
from app.db.models import Guide, Spot, User
from app.routers.auth import router as auth_router
from app.routers.trips import router as trips_router
from app.routers.trip_imports import router as import_router
from app.services import trip_guide_import, wikivoyage


@pytest.fixture()
def import_client():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with sessions() as db:
        db.add_all([Spot(name='西湖', city='杭州', amap_id='hangzhou-1', address='杭州地址', lng=120.14, lat=30.25),
                    Spot(name='西湖', city='福州', amap_id='fuzhou-1', address='福州地址'),
                    Spot(name='公园', city='杭州', amap_id='park-1', address='甲'),
                    Spot(name='公园', city='杭州', amap_id='park-2', address='乙')])
        db.commit()
    app = FastAPI()
    app.include_router(auth_router); app.include_router(trips_router); app.include_router(import_router)
    def override_db():
        db = sessions()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        yield c, sessions
    engine.dispose()


def account(c, name):
    assert c.post('/api/auth/register', json={'username': name, 'password': 'password1'}).status_code == 201
    token = c.post('/api/auth/login', json={'username': name, 'password': 'password1'}).json()['token']
    return {'Authorization': f'Bearer {token}'}


def create_trip(c, headers):
    return c.post('/api/trips', headers=headers, json={'title': '杭州', 'destination': '杭州', 'days': 1}).json()


def test_public_guide_urls_require_the_supported_site_and_article():
    assert wikivoyage.title_from_url('https://zh.wikivoyage.org/zh-hans/%E6%9D%AD%E5%B7%9E#观光') == '杭州'
    for invalid in ('https://example.test/wiki/杭州', 'http://zh.wikivoyage.org/wiki/杭州',
                    'https://zh.wikivoyage.org/wiki/Special:随机', 'https://zh.wikivoyage.org/w/api.php',
                    'https://zh.wikivoyage.org/wiki/杭州?action=edit'):
        with pytest.raises(wikivoyage.SupplierError, match='中文维基导游'):
            wikivoyage.title_from_url(invalid)


def test_public_guide_uses_fixed_api_and_extracts_only_marked_names(monkeypatch):
    monkeypatch.setattr(wikivoyage, '_cache', {})
    requests = []

    def response(request):
        requests.append(request)
        assert str(request.url.copy_with(query=None)) == wikivoyage.ENDPOINT
        assert request.url.params['page'] == '杭州'
        assert request.headers['User-Agent'].startswith('IntelligentTravelAgent/')
        return httpx.Response(200, json={'parse': {
            'title': '杭州', 'revid': 123,
            'text': '<p>正文与描述不进入结果</p><span class="listing-name"><a href="/wiki/西湖">西湖</a></span>'
                    '<span class="listing-name">西湖</span><span class="listing-name"><b>公园</b></span>',
        }})

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        guide = wikivoyage.fetch_guide('杭州', client=client)
        assert guide.names == ('西湖', '公园')
        assert guide.source_url == 'https://zh.wikivoyage.org/w/index.php?oldid=123'
        assert wikivoyage.fetch_guide('杭州', client=client) is guide
    assert len(requests) == 1


def test_public_guide_previews_permissions_before_fetch_and_saves_only_selected(import_client, monkeypatch):
    from app.routers import trip_imports

    c, _ = import_client
    monkeypatch.setattr(trip_imports, '_public_guide_peers', {})
    calls = []

    def fake_fetch(title):
        calls.append(title)
        return wikivoyage.PublicGuide('杭州', 123, ('西湖', '公园', '合成未匹配'))

    monkeypatch.setattr(wikivoyage, 'fetch_guide', fake_fetch)
    owner, other = account(c, 'public_guide_owner'), account(c, 'public_guide_other')
    trip = create_trip(c, owner)
    root = f"/api/trips/{trip['id']}"
    body = {'url': 'https://zh.wikivoyage.org/wiki/杭州', 'version': trip['version']}
    route = root+'/guide-import/public-link-preview'
    assert c.post(route, headers=other, json=body).status_code == 404
    assert c.post(route, headers=owner, json={**body, 'version': trip['version']+1}).status_code == 409
    assert c.post(route, headers=owner, json={**body, 'url': 'https://example.test/wiki/杭州'}).status_code == 422
    assert calls == []
    result = c.post(route, headers=owner, json=body)
    assert result.status_code == 200
    assert result.headers['cache-control'] == 'no-store'
    preview = result.json()
    assert [item['status'] for item in preview['places']] == ['matched', 'ambiguous', 'unmatched']
    assert c.get(root, headers=owner).json() == trip
    guest = c.post('/api/trips/guest-guide/public-link-preview', json={**body, 'destination': '杭州'}).json()
    assert guest['places'][0]['matches'][0]['lng'] == 120.14
    assert guest['guide']['license_url'] == wikivoyage.LICENSE_URL
    source = '维基导游 · CC BY-SA 4.0 · ' + preview['guide']['url']
    applied = c.post(root+'/guide-import/apply', headers=owner, json={'version': trip['version'], 'source': source,
        'places': [{'name': '西湖', 'spot_id': preview['places'][0]['matches'][0]['id']}]})
    assert applied.status_code == 200
    assert applied.json()['added'] == 1
    assert c.get(root, headers=owner).json()['places'][0]['source'] == source
    assert calls == ['杭州', '杭州']


def test_browser_public_guide_snapshot_matches_only_the_requested_title(import_client, monkeypatch):
    from app.routers import trip_imports

    c, _ = import_client
    monkeypatch.setattr(trip_imports, '_public_guide_peers', {})
    monkeypatch.setattr(wikivoyage, 'fetch_guide', lambda *args: pytest.fail('browser snapshot must not fetch again'))
    body = {'url': 'https://zh.wikivoyage.org/wiki/杭州', 'version': 1, 'destination': '杭州',
            'browser_snapshot': {'title': '杭州', 'revision': 456, 'names': ['西湖', '西湖']}}
    route = '/api/trips/guest-guide/public-link-preview'
    response = c.post(route, json=body)
    assert response.status_code == 200
    assert response.json()['places'][0]['matches'][0]['amap_id'] == 'hangzhou-1'
    assert len(response.json()['places']) == 1
    assert response.json()['guide']['url'].endswith('?oldid=456')
    assert c.post(route, json={**body, 'browser_snapshot': {**body['browser_snapshot'], 'title': '北京'}}).status_code == 422
    assert c.post(route, json={**body, 'browser_snapshot': {**body['browser_snapshot'], 'names': ['x'*161]}}).status_code == 422


def test_preview_filters_invented_names_and_never_fetches_links(import_client, monkeypatch):
    c, _ = import_client
    owner, other = account(c, 'import_owner'), account(c, 'import_other')
    trip = create_trip(c, owner)
    path = f"/api/trips/{trip['id']}/guide-import/preview"
    calls = []
    async def fake_chat(messages, **kwargs):
        calls.append(messages)
        return json.dumps({'places': [{'name': '西湖', 'note': '适合散步'},
                                      {'name': '公园', 'note': '有茶馆'},
                                      {'name': '未知地点', 'note': '模型编出的备注'},
                                      {'name': '黄山', 'note': '模型幻觉'}]}, ensure_ascii=False)
    monkeypatch.setattr(trip_guide_import, 'chat', fake_chat)
    body = {'version': trip['version'], 'text': '西湖适合散步。公园有茶馆。未知地点要预约。 https://example.test/story'}
    assert c.post(path, headers=other, json=body).status_code == 404
    response = c.post(path, headers=owner, json=body)
    assert response.status_code == 200, response.text
    data = response.json()
    assert [(p['name'], p['status']) for p in data['places']] == [('西湖', 'matched'), ('公园', 'ambiguous'), ('未知地点', 'unmatched')]
    assert data['places'][0]['matches'][0]['city'] == '杭州'
    assert len(data['places'][1]['matches']) == 2 and data['places'][2]['matches'] == []
    assert data['places'][2]['note'] == ''
    assert c.get(f"/api/trips/{trip['id']}", headers=owner).json() == trip
    assert len(calls) == 1


def test_apply_atomic_merge_preserves_notes_and_unmatched(import_client):
    c, _ = import_client
    owner, other = account(c, 'import_apply'), account(c, 'import_intruder')
    trip = create_trip(c, owner)
    path = f"/api/trips/{trip['id']}/guide-import/apply"
    body = {'version': trip['version'], 'source': '朋友攻略', 'places': [
        {'name': '西湖', 'note': '适合散步', 'spot_id': 1},
        {'name': '未知地点', 'note': '要预约', 'spot_id': None}]}
    assert c.post(path, headers=other, json=body).status_code == 404
    response = c.post(path, headers=owner, json=body)
    assert response.status_code == 200, response.text
    data = response.json(); trip = data['trip']
    assert data['added'] == 2 and data['merged'] == 0
    matched, unknown = trip['places']
    assert (matched['name'], matched['amap_id'], matched['lng'], matched['note'], matched['source']) == ('西湖', 'hangzhou-1', 120.14, '适合散步', '朋友攻略')
    assert unknown['name'] == '未知地点' and unknown['lng'] is None and unknown['note'] == '要预约'
    assert c.post(path, headers=owner, json=body).status_code == 409
    body = {'version': trip['version'], 'source': '另一篇', 'places': [
        {'name': '西湖', 'note': '傍晚看日落', 'spot_id': 1},
        {'name': '西湖', 'note': '租船', 'spot_id': 1}]}
    result = c.post(path, headers=owner, json=body).json()
    trip = result['trip']
    assert len(trip['places']) == 2 and result['merged'] == 2
    assert trip['places'][0]['note'] == '适合散步\n\n傍晚看日落\n\n租船'
    assert trip['places'][0]['source'] == '朋友攻略\n\n另一篇'
    invalid = {'version': trip['version'], 'source': '来源', 'places': [
        {'name': '公园', 'spot_id': 1, 'note': '错误匹配'}]}
    assert c.post(path, headers=owner, json=invalid).status_code == 422
    assert c.get(f"/api/trips/{trip['id']}", headers=owner).json() == trip


def test_import_never_silently_truncates_merged_content(import_client):
    c, _ = import_client
    owner = account(c, 'import_long')
    trip = create_trip(c, owner)
    path = f"/api/trips/{trip['id']}/guide-import/apply"
    body = {'version': trip['version'], 'source': '第一篇', 'places': [{'name': '西湖', 'spot_id': 1, 'note': 'a' * 2000}]}
    trip = c.post(path, headers=owner, json=body).json()['trip']
    body = {'version': trip['version'], 'source': '第二篇', 'places': [{'name': '西湖', 'spot_id': 1, 'note': 'b' * 2000}]}
    assert c.post(path, headers=owner, json=body).status_code == 422
    assert c.get(f"/api/trips/{trip['id']}", headers=owner).json() == trip


def test_retry_invalid_model_json(import_client, monkeypatch):
    c, _ = import_client
    owner = account(c, 'import_retry')
    trip = create_trip(c, owner)
    seen = []
    async def fake_chat(messages, **kwargs):
        seen.append(1)
        return 'bad response' if len(seen) == 1 else '{"places":[{"name":"西湖","note":"散步"}]}'
    monkeypatch.setattr(trip_guide_import, 'chat', fake_chat)
    response = c.post(f"/api/trips/{trip['id']}/guide-import/preview", headers=owner,
                      json={'version': trip['version'], 'text': '西湖散步'})
    assert response.status_code == 200 and len(seen) == 2
    assert response.json()['places'][0]['name'] == '西湖'


def test_preview_rejects_concurrent_edit(import_client, monkeypatch):
    c, sessions = import_client
    owner = account(c, 'import_concurrent')
    trip = create_trip(c, owner)
    from app.db.trip_models import Trip

    async def fake_chat(messages, **kwargs):
        with sessions() as db:
            stored = db.get(Trip, trip['id'])
            stored.title = '已修改'; stored.version += 1
            db.commit()
        return '{"places":[{"name":"西湖","note":""}]}'

    monkeypatch.setattr(trip_guide_import, 'chat', fake_chat)
    response = c.post(f"/api/trips/{trip['id']}/guide-import/preview", headers=owner,
                      json={'version': trip['version'], 'text': '西湖'})
    assert response.status_code == 409
    current = c.get(f"/api/trips/{trip['id']}", headers=owner).json()
    assert current['title'] == '已修改' and current['places'] == []


def test_missing_city_never_auto_selects_a_match(import_client, monkeypatch):
    c, _ = import_client
    owner = account(c, 'import_nocity')
    trip = c.post('/api/trips', headers=owner, json={'title': '旅行', 'days': 1}).json()
    async def fake_chat(messages, **kwargs):
        return '{"places":[{"name":"西湖","note":""}]}'
    monkeypatch.setattr(trip_guide_import, 'chat', fake_chat)
    result = c.post(f"/api/trips/{trip['id']}/guide-import/preview", headers=owner,
                    json={'version': trip['version'], 'text': '西湖'}).json()
    assert result['places'][0]['status'] == 'ambiguous'


def test_site_guides_obey_visibility_and_are_only_imported_after_review(import_client, monkeypatch):
    c, sessions = import_client
    owner, other = account(c, 'site_owner'), account(c, 'site_other')
    trip = create_trip(c, owner)
    with sessions() as db:
        owner_id = db.query(User).filter_by(username='site_owner').one().id
        other_id = db.query(User).filter_by(username='site_other').one().id
        public = Guide(user_id=other_id, title='公开杭州', city='杭州', content='西湖适合散步', is_draft=False)
        own = Guide(user_id=owner_id, title='我的草稿', city='杭州', content='西湖', is_draft=True)
        private = Guide(user_id=other_id, title='别人的草稿', city='杭州', content='秘密', is_draft=True)
        long = Guide(user_id=owner_id, title='长正文', city='杭州', content='长' * 15000, is_draft=True)
        db.add_all([public, own, private, long]); db.commit()
        public_id, own_id, private_id, long_id = public.id, own.id, private.id, long.id
    path = f"/api/trips/{trip['id']}/guide-import/site-guides"
    assert c.get(path, headers=other).status_code == 404
    listing = c.get(path, headers=owner)
    assert listing.headers['cache-control'] == 'no-store'
    assert {entry['id'] for entry in listing.json()['items']} == {public_id, own_id, long_id}
    assert c.get(path, headers=owner, params={'keyword':'%'}).json()['total'] == 0
    assert c.get(f'{path}/{private_id}', headers=owner).status_code == 404
    assert c.get(f'{path}/{own_id}', headers=owner).json()['text'] == '西湖'
    assert len(c.get(f'{path}/{long_id}', headers=owner).json()['text']) == 15000
    fetched = c.get(f'{path}/{public_id}', headers=owner)
    assert fetched.json()['text'] == '西湖适合散步'
    with sessions() as db:
        assert db.get(Guide, public_id).views == 0
    assert c.get(f"/api/trips/{trip['id']}", headers=owner).json() == trip

    async def fake_chat(messages, **kwargs):
        return '{"places":[{"name":"西湖","note":"适合散步"}]}'
    monkeypatch.setattr(trip_guide_import, 'chat', fake_chat)
    base = f"/api/trips/{trip['id']}/guide-import"
    preview = c.post(f'{base}/preview', headers=owner, json={'version': trip['version'], 'text': fetched.json()['text']})
    assert preview.status_code == 200 and preview.json()['places'][0]['name'] == '西湖'
    assert c.get(f"/api/trips/{trip['id']}", headers=owner).json()['places'] == []
    result = c.post(f'{base}/apply', headers=owner, json={'version': trip['version'],
                    'source': f'站内攻略 #{public_id}：公开杭州',
                    'places': [{'name':'西湖', 'note':'适合散步', 'spot_id':1}]})
    assert result.status_code == 200 and result.json()['trip']['places'][0]['source'].startswith('站内攻略 #')


def test_guest_preview_is_review_only_and_keeps_match_coordinates(import_client, monkeypatch):
    from app.routers import trip_imports
    c, _ = import_client
    trip_imports._guest_preview_peers.clear()
    async def fake_chat(messages, **kwargs):
        return json.dumps({'places':[{'name':'西湖','note':'适合散步'}, {'name':'虚构地点','note':''}]}, ensure_ascii=False)
    monkeypatch.setattr(trip_guide_import, 'chat', fake_chat)
    payload = {'version':3, 'destination':'杭州', 'text':'西湖适合散步。'}
    response = c.post('/api/trips/guest-guide-preview', json=payload)
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    result = response.json()
    assert result['version'] == 3 and len(result['places']) == 1
    match = result['places'][0]['matches'][0]
    assert (match['name'], match['amap_id'], match['lng'], match['lat']) == ('西湖','hangzhou-1',120.14,30.25)
    assert c.post('/api/trips/guest-guide-preview', json={**payload, 'text':' '}).status_code == 422
    for _ in range(3): assert c.post('/api/trips/guest-guide-preview', json=payload).status_code == 200
    assert c.post('/api/trips/guest-guide-preview', json=payload).status_code == 429
    trip_imports._guest_preview_peers.clear()


def test_guest_site_guides_never_expose_drafts_or_increment_views(import_client):
    c, sessions = import_client
    owner = account(c, 'guest_guide_author')
    with sessions() as db:
        uid = db.query(User).filter_by(username='guest_guide_author').one().id
        public = Guide(user_id=uid, title='公开杭州', city='杭州', content='西湖风景', is_draft=False)
        draft = Guide(user_id=uid, title='私密草稿', city='杭州', content='秘密正文', is_draft=True)
        db.add_all([public, draft]); db.commit(); public_id, draft_id = public.id, draft.id
    path = '/api/trips/guest-guide/site-guides'
    result = c.get(path)
    assert result.status_code == 200 and result.headers['cache-control'] == 'no-store'
    assert [item['id'] for item in result.json()['items']] == [public_id]
    assert c.get(path, params={'keyword':'%'}).json()['total'] == 0
    assert c.get(f'{path}/{draft_id}').status_code == 404
    # Even a logged-in author cannot accidentally copy a private draft into a local trip here.
    assert c.get(f'{path}/{draft_id}', headers=owner).status_code == 404
    response = c.get(f'{path}/{public_id}')
    assert response.status_code == 200 and response.json()['text'] == '西湖风景'
    with sessions() as db: assert db.get(Guide, public_id).views == 0
