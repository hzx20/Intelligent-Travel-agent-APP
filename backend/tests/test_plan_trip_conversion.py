import json
import httpx
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from sqlalchemy import text
from app.db.models import AiPlan
from app.db.trip_models import Trip, PlanTripConversion
from app.services import trip_routes
from test_trips import account
from test_trip_guest_import import sample


def setup(client):
    c,sessions=client;auth=account(c,'conversion_owner')
    uid=c.get('/api/auth/me',headers=auth).json()['id']
    with sessions() as db:
        p=AiPlan(user_id=uid,session_id='conversion',status='completed',title='历史原件',
                 intent_json='{"destination":"杭州"}',result_json='{"days":[{"theme":"湖边","spots":[{"name":"西湖"}]}]}',chat_json='[{"role":"ai","text":"原文"}]')
        db.add(p);db.commit();pid,revision=p.id,p.revision
    return c,sessions,auth,uid,pid,{'version':1,'revision':revision}


def test_conversion_retry_current_trip_and_read_only_state(client):
    c,sessions,a,uid,pid,body=setup(client);path=f'/api/trips/from-plan/{pid}'
    assert c.get(path+'/conversion',headers=a,params={'revision':body['revision']}).json()['state']=='not_created'
    first=c.post(path+'/convert',headers=a,json=body);assert first.status_code==201,first.text
    r=first.json();assert r['owner_id']==uid and r['plan_revision']==body['revision']
    trip=r['trip'];assert trip['days'][0]['items'][0]['name']=='西湖'
    edited=c.patch(f"/api/trips/{trip['id']}",headers=a,json={'version':1,'title':'转换后编辑'}).json()
    retry=c.post(path+'/convert',headers=a,json=body)
    assert retry.status_code==200 and retry.json()['trip']==edited
    assert retry.headers['cache-control']=='no-store'
    assert c.get(path+'/conversion',headers=a,params={'revision':body['revision']}).json()['trip']==edited
    with sessions() as db:
        p=db.get(AiPlan,pid);assert p.version==1 and p.chat_json=='[{"role":"ai","text":"原文"}]'
        assert db.query(PlanTripConversion).count()==1 and db.query(Trip).count()==1


def test_conversion_concurrency_and_missing_or_wrong_source_identity(client):
    c,sessions,a,_,pid,b=setup(client);path=f'/api/trips/from-plan/{pid}/convert'
    assert c.post(path,headers=a,json={}).status_code==422
    assert c.post(path,headers=a,json={**b,'revision':str(uuid4())}).status_code==409
    assert c.post(path,headers=a,json={**b,'version':2}).status_code==409
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:c.post(path,headers=a,json=b),range(2)))
    assert sorted(r.status_code for r in responses)==[200,201]
    assert responses[0].json()['trip']['id']==responses[1].json()['trip']['id']


def test_conversion_tombstone_and_original_source_removed(client):
    c,sessions,a,_,pid,b=setup(client);path=f'/api/trips/from-plan/{pid}'
    trip=c.post(path+'/convert',headers=a,json=b).json()['trip']
    # Receipt still finds the original trip if its source is later hidden.
    assert c.delete(f'/api/plan/{pid}/content',headers=a,params=b).status_code==200
    assert c.post(path+'/convert',headers=a,json=b).json()['trip']['id']==trip['id']
    with sessions() as db:db.delete(db.get(Trip,trip['id']));db.commit()
    assert c.post(path+'/convert',headers=a,json=b).status_code==410
    with sessions() as db:
        assert db.query(Trip).count()==0 and db.query(PlanTripConversion).one().trip_id is None


def test_conversion_legacy_adoption_and_ownership(client):
    c,sessions,a,_,pid,b=setup(client);other=account(c,'conversion_other');path=f'/api/trips/from-plan/{pid}'
    assert c.post(path+'/convert',headers=other,json=b).status_code==404
    assert c.get(path+'/conversion',headers=other,params={'revision':b['revision']}).status_code==404
    assert c.post(path+'/convert',json=b).status_code==401
    legacy=c.post(path,headers=a);assert legacy.status_code==201
    adopted=c.post(path+'/convert',headers=a,json=b)
    assert adopted.status_code==200 and adopted.json()['trip']==legacy.json()
    assert c.post(path,headers=a).status_code==409


def test_guest_lookup_does_not_create_and_binds_exact_body(client):
    c,sessions=client;a=account(c,'copy_check_owner');other=account(c,'copy_check_other');b=sample()
    path='/api/trips/import-guest'
    not_created=c.post(path+'/check',headers=a,json=b).json()
    assert not_created['state']=='not_created' and not_created['already_imported'] is False
    assert c.get('/api/trips',headers=a).json()['items']==[]
    created=c.post(path,headers=a,json=b).json()
    checked=c.post(path+'/check',headers=a,json=b)
    assert checked.headers['cache-control']=='no-store'
    assert checked.json()['trip']==created['trip'] and checked.json()['guest_version']==b['guest_version']
    assert c.post(path+'/check',headers=other,json=b).json()['state']=='not_created'
    b['trip']['title']='改动原次正文'
    assert c.post(path+'/check',headers=a,json=b).status_code==409


def test_conversion_rolls_back_partial_copy(client,monkeypatch):
    c,sessions,a,_,pid,b=setup(client)
    from sqlalchemy.orm import Session
    original=Session.commit
    def fail_on_receipt(db):
        if any(isinstance(row,PlanTripConversion) for row in db.new):raise RuntimeError('synthetic commit failure')
        return original(db)
    monkeypatch.setattr(Session,'commit',fail_on_receipt)
    # TestClient propagates server exceptions; the request dependency closes and rolls back.
    import pytest
    with pytest.raises(RuntimeError,match='synthetic commit failure'):
        c.post(f'/api/trips/from-plan/{pid}/convert',headers=a,json=b)
    with sessions() as db:assert db.query(Trip).count()==db.query(PlanTripConversion).count()==0


def test_reused_source_id_does_not_adopt_another_revision_conversion(client):
    c,sessions,a,uid,pid,b=setup(client);path=f'/api/trips/from-plan/{pid}'
    old=c.post(path+'/convert',headers=a,json=b).json()['trip']
    with sessions() as db:
        db.execute(text('DELETE FROM ai_plans WHERE id=:id'),{'id':pid})
        replacement=AiPlan(id=pid,user_id=uid,session_id='new-identity',status='completed',result_json='{"days":[{}]}')
        db.add(replacement);db.commit();new_revision=replacement.revision
    # The old receipt may recover its own trip, but a different source identity cannot claim it.
    assert c.post(path+'/convert',headers=a,json=b).json()['trip']['id']==old['id']
    assert c.post(path+'/convert',headers=a,json={'version':1,'revision':new_revision}).status_code==409
    assert c.get(path+'/conversion',headers=a,params={'revision':new_revision}).status_code==409


def test_saved_candidate_handoff_does_not_route_until_explicit_association(client, monkeypatch):
    c, sessions, owner, _, pid, identity = setup(client)
    snapshot = {'days': [{'spots': [
        {'name': '西湖', 'time': '09:00', 'lng': 120.14, 'lat': 30.25, 'amap_id': 'west'},
        {'name': '示例博物馆', 'time': '13:00', 'duration_min': 60, 'type': '室内场馆',
         'verification_status': 'unverified', 'lng': 120.31, 'lat': 30.4, 'amap_id': 'guessed',
         'note': '预留午间休息', 'verification_candidates': [
             {'name': '示例博物馆（北馆区）', 'address': '合成北路1号', 'district': '北区', 'lng': 120.31, 'amap_id': 'guessed'},
             {'name': '示例博物馆（南馆区）', 'address': '合成南路1号', 'district': '南区'}]},
    ]}]}
    with sessions() as db:
        db.get(AiPlan, pid).result_json = json.dumps(snapshot, ensure_ascii=False)
        db.commit()
    conversion = c.post(f'/api/trips/from-plan/{pid}/convert', headers=owner, json=identity)
    assert conversion.status_code == 201
    trip = conversion.json()['trip']
    day = trip['days'][0]
    start, pending = day['items']
    assert pending['lng'] is None and pending['lat'] is None and not pending['amap_id']
    assert len(pending['location_candidates']) == 2
    assert pending['duration_min'] == 60
    assert all(set(label) == {'name', 'address', 'district'} for label in pending['location_candidates'])
    calls = []

    def provider(url, *, params, timeout):
        calls.append(params)
        return httpx.Response(200, request=httpx.Request('GET', url), json={
            'status': '1', 'route': {'paths': [{'distance': '900', 'cost': {'duration': '750'}, 'steps': []}]}})

    monkeypatch.setattr(trip_routes, 'amap_get', provider)
    monkeypatch.setattr(trip_routes.settings, 'amap_api_key', 'isolated-confirmed-route')
    trip_routes._cache.clear()
    path = f"/api/trips/{trip['id']}"
    routing = f"{path}/days/{day['id']}/route"
    params = {'origin_id': start['id'], 'destination_id': pending['id'], 'version': trip['version']}
    assert c.get(routing, headers=owner, params=params).json()['status'] == 'unavailable'
    assert calls == []
    item_path = f"{path}/items/{pending['id']}"
    updated = {**pending, 'version': trip['version'], 'name': '示例博物馆（南馆区）',
               'address': '合成南路1号', 'lng': 120.1251, 'lat': 30.2351, 'amap_id': 'verified-south', 'source': '高德地图'}
    assert c.patch(item_path, headers=owner, json={**updated, 'version': 999}).status_code == 409
    saved = c.patch(item_path, headers=owner, json=updated)
    assert saved.status_code == 200, saved.text
    actual = c.get(path, headers=owner).json()
    item = actual['days'][0]['items'][1]
    assert item['id'] == pending['id'] and item['name'] == updated['name']
    assert item['start_time'] == pending['start_time'] and item['note'] == pending['note'] and item['kind'] == pending['kind']
    assert item['duration_min'] == 60
    assert item['amap_id'] == 'verified-south' and 'location_candidates' not in item
    assert c.get(routing, headers=owner, params=params).status_code == 409
    route = c.get(routing, headers=owner, params={**params, 'version': actual['version']}).json()
    assert route['status'] == 'ok' and route['distance_m'] == 900
    assert len(calls) == 1 and calls[0]['destination'] == '120.125100,30.235100'
    with sessions() as db:
        assert json.loads(db.get(AiPlan, pid).result_json) == snapshot
