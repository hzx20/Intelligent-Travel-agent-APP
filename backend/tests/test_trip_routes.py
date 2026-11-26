"""Route results are restricted to adjacent owned items and never invent paths."""
import httpx

from app.services import trip_routes
from test_trips import account


def setup_trip(c, headers):
    trip = c.post('/api/trips', headers=headers, json={'title': '测试行程', 'days': 1}).json()
    day = trip['days'][0]['id']
    for name, coords in [('西湖', [120.14, 30.25]), ('公园', [120.15, 30.26]), ('活动', None)]:
        data = {'name': name, 'version': trip['version']}
        if coords:
            data.update(lng=coords[0], lat=coords[1])
        trip = c.post(f"/api/trips/{trip['id']}/days/{day}/items", headers=headers, json=data).json()
    return trip, day


def test_adopted_modes_bind_to_current_directed_legs_and_use_trip_version(client):
    c, _ = client
    owner, other = account(c, 'leg_owner'), account(c, 'leg_other')
    trip = c.post('/api/trips', headers=owner, json={'title': '分段交通'}).json()
    root = f"/api/trips/{trip['id']}"
    day_path = f"{root}/days/{trip['days'][0]['id']}"
    for data in [{'name': '甲馆', 'lng': 120.1, 'lat': 30.2}, {'name': '午休', 'kind': '休息'},
                 {'name': '乙馆', 'lng': 120.2, 'lat': 30.2}, {'name': '丙馆', 'lng': 120.3, 'lat': 30.2},
                 {'name': '待核实餐厅', 'kind': '餐饮'}]:
        result = c.post(day_path + '/items', headers=owner, json={**data, 'version': trip['version']})
        assert result.status_code == 201, result.text
        trip = result.json()
    first, rest, middle, last, meal = [item['id'] for item in trip['days'][0]['items']]
    url = day_path + '/route-preference'
    choose = lambda origin, dest, mode, headers=owner: c.patch(url, headers=headers, json={
        'origin_id': origin, 'destination_id': dest, 'mode': mode, 'version': trip['version']})
    assert choose(first, middle, 'walking', other).status_code == 404
    assert choose(first, rest, 'walking').status_code == 404
    assert choose(last, meal, 'driving').status_code == 422
    assert choose(first, last, 'walking').status_code == 404
    saved = choose(first, middle, 'walking')
    assert saved.status_code == 200, saved.text
    assert choose(first, middle, None).status_code == 409
    trip = saved.json()
    trip = choose(middle, last, 'driving').json()
    choices = lambda: c.get(root, headers=owner).json()['days'][0]['route_preferences']
    assert choices() == {f'{first}:{middle}': 'walking', f'{middle}:{last}': 'driving'}
    first_data = trip['days'][0]['items'][0]
    trip = c.patch(f'{root}/items/{first}', headers=owner, json={**first_data, 'start_time': '09:00', 'version': trip['version']}).json()
    assert choices()[f'{first}:{middle}'] == 'walking'
    trip = c.patch(f'{root}/items/{first}', headers=owner, json={**first_data, 'lng': 120.4, 'version': trip['version']}).json()
    trip = c.patch(f'{root}/items/{first}', headers=owner, json={**first_data, 'version': trip['version']}).json()
    assert choices() == {f'{middle}:{last}': 'driving'}
    trip = choose(first, middle, 'walking').json()
    trip = c.post(f'{root}/items/{last}/move', headers=owner, json={'version': trip['version'], 'day_id': trip['days'][0]['id'], 'index': 0}).json()
    assert choices() == {f'{first}:{middle}': 'walking'}
    assert choose(first, middle, None).status_code == 200
    assert choices() == {}


def test_route_ownership_adjacency_and_provider_cache(client, monkeypatch):
    c, _ = client
    owner, other = account(c, 'route_owner'), account(c, 'route_other')
    trip, day = setup_trip(c, owner)
    ids = [item['id'] for item in trip['days'][0]['items']]
    endpoint = f"/api/trips/{trip['id']}/days/{day}/route"
    params = {'origin_id': ids[0], 'destination_id': ids[1], 'mode': 'walking'}
    calls = []

    def fake_get(url, params, timeout):
        calls.append((url, params.copy()))
        return httpx.Response(200, request=httpx.Request('GET', url), json={
            'status': '1', 'route': {'paths': [{'distance': '1234', 'cost': {'duration': '720'},
                                                'steps': [{'polyline': '120.14,30.25;120.15,30.26', 'navi': {'walk_type': '20'}},
                                                          {'navi': {'walk_type': 21}}]}]}})

    monkeypatch.setattr(trip_routes.settings, 'amap_api_key', 'test-key')
    monkeypatch.setattr(trip_routes.httpx, 'get', fake_get)
    monkeypatch.setattr(trip_routes.time, 'sleep', lambda _: None)
    trip_routes._cache.clear()
    assert c.get(endpoint, params=params, headers=other).status_code == 404
    assert c.get(endpoint, params={**params, 'destination_id': ids[2]}, headers=owner).status_code == 404
    first = c.get(endpoint, params=params, headers=owner).json()
    assert first['status'] == 'ok' and first['source'] == '高德地图'
    assert first['distance_m'] == 1234 and first['duration_s'] == 720
    assert first['polyline'] == [[120.14, 30.25], [120.15, 30.26]] and first['cached'] is False
    assert calls[0][0].endswith('/walking') and calls[0][1]['show_fields'] == 'cost,polyline,navi'
    assert first['walking_features'] == ['stairs', 'slope']
    assert 'test-key' not in str(first)
    assert c.get(endpoint, params=params, headers=owner).json()['cached'] is True
    assert len(calls) == 1
    driving = c.get(endpoint, params={**params, 'mode': 'driving'}, headers=owner).json()
    assert driving['status'] == 'ok' and 'walking_features' not in driving
    assert calls[1][1]['show_fields'] == 'cost,polyline'
    assert len(calls) == 2
    missing = c.get(endpoint, params={'origin_id': ids[1], 'destination_id': ids[2]}, headers=owner).json()
    assert missing['status'] == 'unavailable' and '坐标' in missing['message']
    assert len(calls) == 2
    assert c.get(f"/api/trips/{trip['id']}", headers=owner).json()['version'] == trip['version']


def test_route_failures_do_not_become_fabricated_estimates(client, monkeypatch):
    c, _ = client
    owner = account(c, 'route_failure')
    trip, day = setup_trip(c, owner)
    start, end = trip['days'][0]['items'][:2]
    endpoint = f"/api/trips/{trip['id']}/days/{day}/route"
    params = {'origin_id': start['id'], 'destination_id': end['id']}
    monkeypatch.setattr(trip_routes.settings, 'amap_api_key', '')
    trip_routes._cache.clear()
    assert '未配置' in c.get(endpoint, params=params, headers=owner).json()['message']
    monkeypatch.setattr(trip_routes.settings, 'amap_api_key', 'test-key')
    monkeypatch.setattr(trip_routes.time, 'sleep', lambda _: None)
    monkeypatch.setattr(trip_routes.httpx, 'get', lambda url, **kw: httpx.Response(200, request=httpx.Request('GET', url), json={'status': '1', 'route': {'paths': [{'distance': '100'}]}}))
    response = c.get(endpoint, params=params, headers=owner).json()
    assert response['status'] == 'unavailable' and 'distance_m' not in response
    assert not trip_routes._cache


def test_polyline_missing_is_an_explicit_no_geometry():
    result = trip_routes._parse_path({'status': '1', 'route': {'paths': [{'distance': '12', 'cost': {'duration': '10'}, 'steps': []}]}})
    assert result['polyline'] == [] and result['duration_s'] == 10


def test_route_can_connect_before_and_after_a_rest_but_not_an_unlocated_meal(client, monkeypatch):
    c, _ = client
    owner = account(c, 'route_rest_owner')
    trip, day_id = setup_trip(c, owner)
    before, after, _ = trip['days'][0]['items']
    path = f"/api/trips/{trip['id']}"
    added = c.post(f'{path}/days/{day_id}/items', headers=owner, json={
        'version': trip['version'], 'name':'午休', 'kind':'休息', 'duration_min':30}).json()
    rest = added['days'][0]['items'][-1]
    moved = c.post(f"{path}/items/{rest['id']}/move", headers=owner, json={
        'version':added['version'], 'day_id':day_id, 'index':1}).json()
    calls = []
    def route(origin, destination, mode):
        calls.append((origin.id,destination.id,mode))
        return {'distance_m':200,'duration_s':120,'polyline':[],'queried_at':'2026-11-18T00:00:00Z','cached':False}
    monkeypatch.setattr(trip_routes, 'get_route', route)
    query = f'{path}/days/{day_id}/route'
    endpoints = {'origin_id':before['id'], 'destination_id':after['id']}
    assert c.get(query,headers=owner,params={**endpoints,'version':moved['version']}).json()['status']=='ok'
    assert c.get(query,headers=owner,params={**endpoints,'destination_id':rest['id']}).status_code==404
    changed = c.patch(f"{path}/items/{rest['id']}",headers=owner,json={
        'version':moved['version'],'name':'餐厅待定','kind':'餐饮'}).json()
    assert c.get(query,headers=owner,params={**endpoints,'version':changed['version']}).status_code==404
    assert calls==[(before['id'],after['id'],'walking')]


def test_route_rejects_changes_before_and_during_provider_request(client, monkeypatch):
    from app.db.trip_models import Trip
    c, sessions = client
    owner = account(c, 'route_version_owner')
    trip, day = setup_trip(c, owner)
    start, end = trip['days'][0]['items'][:2]
    path = f"/api/trips/{trip['id']}/days/{day}/route"
    params = {'origin_id':start['id'], 'destination_id':end['id'], 'version':trip['version']}
    calls = []
    def changing_route(origin, destination, mode):
        calls.append(True)
        with sessions() as db:
            current = db.get(Trip, trip['id'])
            current.version += 1
            db.commit()
        return {'distance_m':100, 'duration_s':60, 'polyline':[[120,30],[121,31]], 'queried_at':'2026-11-09T00:00:00Z', 'cached':False}
    monkeypatch.setattr(trip_routes, 'get_route', changing_route)
    assert c.get(path, headers=owner, params={**params, 'version':trip['version']-1}).status_code == 409
    assert calls == []
    response = c.get(path, headers=owner, params=params)
    assert response.status_code == 409 and 'polyline' not in response.json()
    assert len(calls) == 1


def test_route_response_is_private_and_not_cached(client, monkeypatch):
    c, _ = client
    owner = account(c, 'route_nocache_owner')
    trip, day = setup_trip(c, owner)
    start, end = trip['days'][0]['items'][:2]
    monkeypatch.setattr(trip_routes, 'get_route', lambda *args: {'distance_m':1, 'duration_s':1, 'polyline':[], 'queried_at':'2026-11-09T00:00:00Z', 'cached':False})
    response = c.get(f"/api/trips/{trip['id']}/days/{day}/route", headers=owner,
                     params={'origin_id':start['id'], 'destination_id':end['id'], 'version':trip['version']})
    assert response.status_code == 200 and response.headers['cache-control'] == 'private, no-store'


def test_guest_route_uses_only_supplied_coordinates_and_safe_results(client, monkeypatch):
    from app.routers import map as map_router
    c, _ = client
    map_router._guest_route_peers.clear()
    calls = []
    def fake_route(origin, destination, mode):
        calls.append((origin.lng, origin.lat, origin.amap_id, destination.lng, destination.lat, mode))
        return {'distance_m':1200, 'duration_s':900, 'polyline':[[120.14,30.25],[120.15,30.26]],
                'queried_at':'2026-11-14T00:00:00Z', 'cached':False}
    monkeypatch.setattr(trip_routes, 'get_route', fake_route)
    body = {'origin':{'lng':120.14,'lat':30.25,'amap_id':'start'},
            'destination':{'lng':120.15,'lat':30.26}, 'mode':'walking'}
    response = c.post('/api/map/guest-route', json=body)
    assert response.status_code == 200 and response.headers['cache-control'] == 'private, no-store'
    assert calls == [(120.14,30.25,'start',120.15,30.26,'walking')]
    assert response.json()['status'] == 'ok' and response.json()['source'] == '高德地图'
    assert c.post('/api/map/guest-route', json={**body,'mode':'flight'}).status_code == 422
    assert c.post('/api/map/guest-route', json={**body,'origin':{'lng':181,'lat':30}}).status_code == 422
    assert c.post('/api/map/guest-route', json={**body,'origin':{'lng':120,'lat':91}}).status_code == 422
    assert len(calls) == 1


def test_guest_route_failure_and_rate_limit_are_explicit(client, monkeypatch):
    from app.routers import map as map_router
    c, _ = client
    map_router._guest_route_peers.clear()
    def unavailable(*args): raise trip_routes.RouteUnavailable('模拟路线不可用')
    monkeypatch.setattr(trip_routes, 'get_route', unavailable)
    body = {'origin':{'lng':120,'lat':30}, 'destination':{'lng':121,'lat':31}}
    first = c.post('/api/map/guest-route', json=body)
    assert first.status_code == 200 and first.json() == {'status':'unavailable', 'message':'模拟路线不可用'}
    for _ in range(29): assert c.post('/api/map/guest-route', json=body).status_code == 200
    assert c.post('/api/map/guest-route', json=body).status_code == 429
    map_router._guest_route_peers.clear()
