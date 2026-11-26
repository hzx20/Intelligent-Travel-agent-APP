"""Map candidates are explicit, bounded and validated before use."""
import httpx
from app.services import map_places
from test_trips import account


class FakeResponse:
    def __init__(self, payload): self.payload = payload
    def raise_for_status(self): pass
    def json(self): return self.payload


def test_map_candidates_are_validated_and_credentials_hidden(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(map_places.settings, 'amap_api_key', 'private-test-key')
    monkeypatch.setattr(map_places, '_last_request', 0.0)
    calls = []
    def fake_get(url, params, timeout):
        calls.append((url, params, timeout))
        return FakeResponse({'status':'1', 'pois':[
            {'id':'valid', 'name':'西湖', 'location':'120.14,30.25', 'address':'湖滨', 'cityname':'杭州市', 'adname':'西湖区'},
            {'id':'valid', 'name':'重复', 'location':'120.14,30.25'},
            {'id':'invalid', 'name':'坏坐标', 'location':'nan,30'},
            {'id':'missing', 'name':'缺坐标', 'location':[]},
            {'id':'', 'name':'缺标识', 'location':'120,30'},
        ]})
    monkeypatch.setattr(map_places.httpx, 'get', fake_get)
    response = c.post('/api/map/place-search', json={'keyword':' 西湖 ', 'city':' 杭州 '})
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    assert response.json()['items'] == [{'amap_id':'valid','name':'西湖','lng':120.14,'lat':30.25,
                                         'address':'湖滨','city':'杭州市','district':'西湖区','source':'高德地图'}]
    assert calls[0][1]['city_limit'] == 'true' and calls[0][1]['keywords'] == '西湖'
    assert 'private-test-key' not in response.text
    assert c.post('/api/map/place-search', json={'keyword':'西湖', 'city':'杭州'}).status_code == 503
    assert c.post('/api/map/place-search', json={'keyword':'西湖', 'city':' '}).status_code == 422
    assert c.post('/api/map/place-search', json={'keyword':'x'*81, 'city':'杭州'}).status_code == 422


def test_map_provider_failure_is_safe_and_local_search_still_works(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(map_places.settings, 'amap_api_key', 'private-test-key')
    monkeypatch.setattr(map_places, '_last_request', 0.0)
    def fail(*args, **kwargs):
        raise httpx.ConnectError('provider secret diagnostic')
    monkeypatch.setattr(map_places.httpx, 'get', fail)
    result = c.post('/api/map/place-search', json={'keyword':'西湖', 'city':'杭州'})
    assert result.status_code == 503 and 'provider secret' not in result.text
    assert c.get('/api/spots', params={'keyword':'西湖', 'city':'杭州'}).status_code == 200


def test_selected_map_candidate_can_be_collected_and_scheduled(client, monkeypatch):
    c, _ = client
    owner = account(c, 'poi_owner')
    monkeypatch.setattr(map_places.settings, 'amap_api_key', 'test-key')
    monkeypatch.setattr(map_places, '_last_request', 0.0)
    monkeypatch.setattr(map_places.httpx, 'get', lambda *args, **kwargs: FakeResponse({'status':'1', 'pois':[
        {'id':'poi123', 'name':'西湖', 'location':'120.14,30.25', 'address':'湖滨'}]}))
    candidate = c.post('/api/map/place-search', json={'keyword':'西湖', 'city':'杭州'}).json()['items'][0]
    trip = c.post('/api/trips', headers=owner, json={'title':'旅行'}).json()
    fields = {key: candidate[key] for key in ('name', 'address', 'lng', 'lat', 'amap_id', 'source')}
    added = c.post(f"/api/trips/{trip['id']}/places", headers=owner, json={**fields, 'version':trip['version']})
    assert added.status_code == 201
    trip = added.json()
    place = trip['places'][0]
    scheduled = c.post(f"/api/trips/{trip['id']}/places/{place['id']}/schedule", headers=owner,
                       json={'version':trip['version'], 'day_id':trip['days'][0]['id']})
    assert scheduled.status_code == 200
    item = scheduled.json()['days'][0]['items'][0]
    assert (item['name'], item['amap_id'], item['source'], item['lng']) == ('西湖', 'poi123', '高德地图', 120.14)


def test_nearby_food_is_scoped_by_location_and_category_without_booking_claims(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(map_places.settings, 'amap_api_key', 'nearby-private-key')
    monkeypatch.setattr(map_places, '_last_request', 0.0)
    calls = []

    def provider(url, *, params, timeout):
        calls.append((url, params, timeout))
        return FakeResponse({'status':'1', 'pois':[
            {'id':'far', 'name':'远处餐厅', 'typecode':'050101', 'location':'120.25,30.25'},
            {'id':'valid2', 'name':'餐厅乙', 'typecode':'050301', 'location':'120.15,30.25', 'distance':'1'},
            {'id':'museum', 'name':'博物馆', 'typecode':'140100', 'location':'120.14,30.25'},
            {'id':'tea', 'name':'附近茶饮', 'typecode':'050600', 'location':'120.1401,30.25'},
            {'id':'origin', 'name':'参考餐厅', 'typecode':'050101', 'location':'120.14,30.25'},
            {'id':'valid1', 'name':'餐厅甲', 'typecode':'050100', 'location':'120.141,30.25',
             'business':{'opentime_today':'10:00-20:00','cost':'60'}},
        ]})

    monkeypatch.setattr(map_places, 'amap_get', provider)
    origin = {'lng':120.14,'lat':30.25,'amap_id':'origin'}
    path = '/api/map/place-search'
    assert c.post(path, json={'category':'hotel','origin':origin}).status_code == 422
    assert c.post(path, json={'category':'food','origin':{**origin,'lat':91}}).status_code == 422
    assert c.post(path, json={'category':'food','origin':origin,'radius':100000}).status_code == 422
    assert not calls
    response = c.post(path, json={'category':'food','origin':origin})
    assert response.status_code == 200 and response.headers['Cache-Control'] == 'no-store'
    matches = response.json()['items']
    assert [(item['amap_id'], item['straight_line_m']) for item in matches] == [('valid1', 96), ('valid2', 961)]
    assert not any(field in matches[0] for field in ('business', 'cost', 'opentime_today'))
    assert 'nearby-private-key' not in response.text and len(calls) == 1
    url, params, _ = calls[0]
    assert url == 'https://restapi.amap.com/v5/place/around'
    assert params['types'] == '050100|050200|050300' and params['radius'] == 1500 and params['sortrule'] == 'distance'
    assert params['location'] == '120.140000,30.250000' and 'amap_id' not in params and 'keywords' not in params


def test_nearby_exhibitions_use_only_relevant_results_and_share_provider_busy_status(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(map_places.settings, 'amap_api_key', 'test-key')
    monkeypatch.setattr(map_places, '_last_request', 0.0)

    def provider(url, *, params, timeout):
        assert params['types'] == '140100|140200|140400'
        return FakeResponse({'status':'1', 'pois':[
            {'id':'faculty', 'name':'附近学校', 'typecode':'141200', 'location':'120.14,30.25'},
            {'id':'closed', 'name':'展馆候选', 'typecode':'140200', 'location':'120.145,30.25'},
        ]})

    monkeypatch.setattr(map_places, 'amap_get', provider)
    body = {'category':'museum','origin':{'lng':120.14,'lat':30.25}}
    result = c.post('/api/map/place-search', json=body)
    assert [row['amap_id'] for row in result.json()['items']] == ['closed']
    assert c.post('/api/map/place-search', json={'keyword':'博物馆','city':'杭州'}).status_code == 503
