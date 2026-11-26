"""The editable trip lifecycle, ownership, concurrency and legacy conversion."""
import json

from app.db.models import AiPlan


def account(c, name):
    assert c.post('/api/auth/register', json={'username': name, 'password': 'password1'}).status_code == 201
    token = c.post('/api/auth/login', json={'username': name, 'password': 'password1'}).json()['token']
    return {'Authorization': f'Bearer {token}'}


def test_trip_edit_move_and_conflict(client):
    c, _ = client
    a, b = account(c, 'trip_a'), account(c, 'trip_b')
    assert c.get('/api/trips').status_code == 401
    response = c.post('/api/trips', headers=a, json={'title': '杭州之旅', 'destination': '杭州', 'days': 2})
    assert response.status_code == 201
    trip = response.json()
    trip_id, first, second = trip['id'], trip['days'][0]['id'], trip['days'][1]['id']
    assert len(trip['days']) == 2
    assert c.get(f'/api/trips/{trip_id}', headers=b).status_code == 404
    url = f'/api/trips/{trip_id}/days/{first}/items'
    response = c.post(url, headers=a, json={'version': 1, 'name': '西湖', 'lng': 120.14, 'lat': 30.25})
    assert response.status_code == 201
    trip = response.json()
    item_id = trip['days'][0]['items'][0]['id']
    assert trip['version'] == 2
    assert c.post(url, headers=a, json={'version': 1, 'name': '旧页面的修改'}).status_code == 409
    assert c.post(f'/api/trips/{trip_id}/items/{item_id}/move', headers=b,
                  json={'version': 2, 'day_id': second, 'index': 0}).status_code == 404
    moved = c.post(f'/api/trips/{trip_id}/items/{item_id}/move', headers=a,
                   json={'version': 2, 'day_id': second, 'index': 0}).json()
    assert moved['days'][0]['items'] == []
    assert moved['days'][1]['items'][0]['name'] == '西湖'
    removed = c.post(f'/api/trips/{trip_id}/items/{item_id}/delete', headers=a,
                     json={'version': 3}).json()
    assert removed['version'] == 4 and removed['days'][1]['items'] == []
    assert c.get(f'/api/trips/{trip_id}', headers=a).json() == removed


def test_import_is_explicit_and_preserves_original(client):
    c, Session = client
    a, b = account(c, 'owner_a'), account(c, 'owner_b')
    uid = c.get('/api/auth/me', headers=a).json()['id']
    raw = {'title': '杭州两日', 'days': [{'theme': '湖边',
           'spots': [{'name': '西湖', 'time': '09:00', 'lng': 120.14, 'lat': 30.25}],
           'items': [{'poi': '西湖', 'time': '09:00'}, {'activity': '午休', 'time': '13:00'}]},
           {'spots': [{'name': '灵隐寺'}]}]}
    with Session() as db:
        plan = AiPlan(user_id=uid, session_id='old', status='completed',
                      intent_json='{"destination":"杭州"}', result_json=json.dumps(raw, ensure_ascii=False))
        db.add(plan)
        db.commit()
        plan_id = plan.id
    path = f'/api/trips/from-plan/{plan_id}'
    assert c.post(path, headers=b).status_code == 404
    response = c.post(path, headers=a)
    assert response.status_code == 201
    trip = response.json()
    assert trip['destination'] == '杭州' and trip['source_plan_id'] == plan_id
    assert [item['name'] for item in trip['days'][0]['items']] == ['西湖', '午休']
    assert c.post(path, headers=a).status_code == 409
    with Session() as db:
        assert json.loads(db.get(AiPlan, plan_id).result_json) == raw


def test_pending_places_and_validation(client):
    c, _ = client
    a = account(c, 'places_a')
    trip = c.post('/api/trips', headers=a, json={'title': '周末', 'days': 1}).json()
    path = f"/api/trips/{trip['id']}/places"
    assert c.post(path, headers=a, json={'version': 1, 'name': '公园', 'lng': 120}).status_code == 422
    trip = c.post(path, headers=a, json={'version': 1, 'name': '公园', 'amap_id': 'poi-1', 'source': '景点库'}).json()
    assert trip['places'][0]['name'] == '公园' and trip['version'] == 2
    assert c.post(path, headers=a, json={'version': 2, 'name': '公园', 'amap_id': 'poi-1'}).status_code == 409
    place_id, day_id = trip['places'][0]['id'], trip['days'][0]['id']
    scheduled = c.post(f"{path}/{place_id}/schedule", headers=a,
                       json={'version': 2, 'day_id': day_id})
    assert scheduled.status_code == 200
    trip = scheduled.json()
    assert trip['places'] == [] and trip['days'][0]['items'][0]['amap_id'] == 'poi-1'
    assert trip['days'][0]['items'][0]['source'] == '景点库'
    assert trip['version'] == 3
    assert c.post(f"{path}/{place_id}/schedule", headers=a,
                  json={'version': 3, 'day_id': day_id}).status_code == 404


def test_edit_day_theme_with_permissions_and_versions(client):
    c, _ = client
    owner, other = account(c, 'day_theme_owner'), account(c, 'day_theme_other')
    trip = c.post('/api/trips', headers=owner, json={'title':'旅行', 'days':2}).json()
    tid, day = trip['id'], trip['days'][1]['id']
    path = f'/api/trips/{tid}/days/{day}'
    data = {'version':trip['version'], 'theme':' 西湖慢游 '}
    assert c.patch(path, headers=other, json=data).status_code == 404
    assert c.patch(path, headers=owner, json={**data, 'theme':'x'*101}).status_code == 422
    response = c.patch(path, headers=owner, json=data)
    assert response.status_code == 200
    changed = response.json()
    assert changed['days'][1]['theme'] == '西湖慢游'
    assert c.patch(path, headers=owner, json=data).status_code == 409
    assert c.post(f'{path}/remove', headers=owner, json={'version':changed['version']}).status_code == 409
    sharing = f'/api/trips/{tid}/sharing'
    invite = c.post(f'{sharing}/links', headers=owner, json={'mode':'edit'}).json()
    assert c.post('/api/trip-sharing/accept', headers=other, json={'token':invite['token']}).status_code == 200
    current = c.get(f'/api/trips/{tid}', headers=other).json()
    response = c.patch(path, headers=other, json={'version':current['version'], 'theme':''})
    assert response.status_code == 200 and response.json()['days'][1]['theme'] == ''
    assert c.post(f'{path}/remove', headers=owner, json={'version':response.json()['version']}).status_code == 200
