"""A local trip is copied atomically, with stable retry identity."""
from copy import deepcopy
from test_trips import account


def sample():
    trip = {'id': 'guest-abc123', 'version': 4, 'title': '杭州', 'destination': '杭州', 'start_date': '2026-12-01',
            'status': 'planning', 'days': [{'id': 1, 'day_number': 1, 'theme': '湖边',
                    'items': [{'id': 3, 'sort_order': 0, 'name': '西湖', 'lng': 120.14, 'lat': 30.25, 'note': '早去'}]},
                    {'id': 2, 'day_number': 2, 'theme': '', 'items': []}],
            'places': [{'id': 4, 'name': '灵隐寺', 'lng': 120.1, 'lat': 30.24, 'amap_id': 'abc', 'source': '景点库'}]}
    return {'guest_id': trip['id'], 'guest_version': trip['version'], 'trip': trip}


def test_import_is_atomic_idempotent_and_owned(client):
    c, _ = client
    owner, other = account(c, 'guest_owner'), account(c, 'guest_other')
    body = sample()
    path = '/api/trips/import-guest'
    assert c.post(path, json=body).status_code == 401
    first = c.post(path, headers=owner, json=body)
    assert first.status_code == 200, first.text
    result = first.json()
    trip = result['trip']
    assert not result['already_imported'] and trip['days'][0]['items'][0]['note'] == '早去'
    assert trip['places'][0]['amap_id'] == 'abc' and trip['days'][1]['day_number'] == 2
    again = c.post(path, headers=owner, json=body).json()
    assert again['already_imported'] and again['trip']['id'] == trip['id']
    modified = deepcopy(body); modified['trip']['title'] = '不同内容'
    assert c.post(path, headers=owner, json=modified).status_code == 409
    assert len(c.get('/api/trips', headers=owner).json()['items']) == 1
    other_result = c.post(path, headers=other, json=body).json()
    assert other_result['trip']['id'] != trip['id']
    assert c.get(f"/api/trips/{trip['id']}", headers=other).status_code == 404


def test_invalid_snapshot_never_creates_partial_trip(client):
    c, _ = client
    owner = account(c, 'guest_invalid')
    body = sample()
    body['trip']['days'][1]['day_number'] = 5
    assert c.post('/api/trips/import-guest', headers=owner, json=body).status_code == 422
    assert c.get('/api/trips', headers=owner).json()['items'] == []


def test_reservations_copy_atomically_and_keep_links(client):
    c, _ = client
    owner = account(c, 'guest_reservation_owner')
    body = sample()
    body['trip']['reservations'] = [
        {'kind':'住宿', 'title':'酒店', 'status':'已预订', 'reference':'TEST-123',
         'start_date':'2026-12-01', 'end_date':'2026-12-02', 'location':{'day_number':1, 'item_index':0}},
        {'kind':'门票', 'title':'旧票', 'status':'已取消', 'item_name':'已删除安排', 'location':None},
    ]
    invalid = deepcopy(body)
    invalid['trip']['reservations'][0]['location']['item_index'] = 99
    assert c.post('/api/trips/import-guest', headers=owner, json=invalid).status_code == 422
    assert c.get('/api/trips', headers=owner).json()['items'] == []
    response = c.post('/api/trips/import-guest', headers=owner, json=body)
    assert response.status_code == 200, response.text
    trip = response.json()['trip']
    path = f"/api/trips/{trip['id']}/reservations"
    records = c.get(path, headers=owner).json()['items']
    linked = next(r for r in records if r['title'] == '酒店')
    detached = next(r for r in records if r['title'] == '旧票')
    assert linked['item_id'] == trip['days'][0]['items'][0]['id']
    assert linked['item_name'] == '西湖' and linked['reference'] == 'TEST-123'
    assert detached['item_id'] is None and detached['item_name'] == '已删除安排'
    assert c.post('/api/trips/import-guest', headers=owner, json=body).json()['already_imported']
    assert len(c.get(path, headers=owner).json()['items']) == 2


def test_legacy_import_hash_compatible_with_empty_reservations(client):
    import hashlib
    import json
    from app.db.guest_import_models import GuestTripImport
    from app.routers.trip_guest_import import GuestSnapshot
    c, sessions = client
    owner = account(c, 'guest_legacy_reservation')
    body = sample()
    first = c.post('/api/trips/import-guest', headers=owner, json=body)
    assert first.status_code == 200
    previous_snapshot = GuestSnapshot.model_validate(body['trip']).model_dump(mode='json')
    previous_snapshot.pop('reservations')
    previous_snapshot.pop('walking_preferences')
    for day in previous_snapshot['days']:
        day.pop('route_preferences')
    old_digest = hashlib.sha256(json.dumps(previous_snapshot, ensure_ascii=False, sort_keys=True,
                                          separators=(',', ':')).encode('utf-8')).hexdigest()
    with sessions() as db:
        assert db.query(GuestTripImport).one().payload_hash == old_digest
    body['trip']['reservations'] = []
    body['trip']['walking_preferences'] = {}
    second = c.post('/api/trips/import-guest', headers=owner, json=body)
    assert second.status_code == 200 and second.json()['already_imported']
    assert c.get('/api/trips/guest-import-capabilities', headers=owner).json()['reservation_records'] is True


def test_import_copies_walking_preferences_and_binds_them_to_the_original_version(client):
    c, _ = client
    owner = account(c, 'guest_walking_owner')
    body = sample()
    preferences = {'daily_limit_m': 3200, 'check_climbs': True}
    body['trip']['walking_preferences'] = preferences
    path = '/api/trips/import-guest'
    copied = c.post(path, headers=owner, json=body)
    assert copied.status_code == 200
    assert copied.json()['trip']['walking_preferences'] == preferences
    assert c.post(path+'/check', headers=owner, json=body).json()['trip']['walking_preferences'] == preferences
    body['trip']['walking_preferences']['daily_limit_m'] = 2000
    assert c.post(path, headers=owner, json=body).status_code == 409


def test_import_remaps_adopted_legs_and_rejects_changed_or_invalid_snapshot(client):
    c, _ = client
    owner = account(c, 'guest_route_import')
    path = '/api/trips/import-guest'
    body = sample()
    day = body['trip']['days'][0]
    day['items'] += [{'id': 5, 'name': '休息', 'kind': '休息'}, {'id': 6, 'name': '目的地', 'lng': 120.12, 'lat': 30.25}]
    day['route_preferences'] = {'0:1': 'walking'}
    assert c.post(path, headers=owner, json=body).status_code == 422
    day['route_preferences'] = {'0:2': 'walking'}
    copied = c.post(path, headers=owner, json=body)
    assert copied.status_code == 200, copied.text
    result = copied.json()['trip']['days'][0]
    first, _, last = [item['id'] for item in result['items']]
    assert result['route_preferences'] == {f'{first}:{last}': 'walking'}
    checked = c.post(path+'/check', headers=owner, json=body)
    assert checked.json()['trip']['days'][0] == result
    day['route_preferences'] = {'0:2': 'driving'}
    assert c.post(path, headers=owner, json=body).status_code == 409
