"""Generic activities never become arbitrary POIs or retain guessed locations."""
import copy
import json
import httpx

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.models import Spot
from app.services import map_verify
from test_plan_trip_conversion import setup


@pytest.mark.parametrize('name', ['附近餐馆', '周边餐厅', '酒店', '杭州', '杭州市', '杭州酒店', '午餐', '休息'])
def test_placeholders_skip_search_and_substitution_even_with_populated_library(monkeypatch, name):
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    before = {'days': [{'day': 1, 'spots': [{'name': name, 'lng': 120.1, 'lat': 30.2,
        'amap_id': 'guessed', 'address': '模型猜测', 'note': '保留说明', 'time': '12:00'}]}]}
    original = copy.deepcopy(before)
    def forbidden(*args, **kwargs):
        pytest.fail('A placeholder must not query or select an arbitrary place')
    monkeypatch.setattr(map_verify, '_amap_search', forbidden)
    with Session(engine) as db:
        db.add(Spot(name='西湖', city='杭州', amap_id='W001', lng=120.14, lat=30.25))
        db.commit()
        result, logs = map_verify.verify_itinerary_spots(before, '杭州', db, amap_enabled=True)
    engine.dispose()
    spot = result['days'][0]['spots'][0]
    assert spot['name'] == name and spot['note'] == '保留说明' and spot['time'] == '12:00'
    assert spot['lng'] is None and spot['lat'] is None and spot['amap_id'] == '' and spot['address'] == ''
    assert spot['verification_status'] == 'unverified' and '具体地点' in spot['verification_note']
    assert logs[0]['action'] == 'unverified' and before == original


def test_real_named_hotel_and_poi_are_still_matched_without_treating_suffix_as_generic():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Spot(name='西湖国宾馆', city='杭州', amap_id='HOTEL001', lng=120.13, lat=30.24))
        db.commit()
        result, logs = map_verify.verify_itinerary_spots(
            {'days': [{'spots': [{'name': '西湖国宾馆'}]}]}, '杭州', db, amap_enabled=False)
        spot = result['days'][0]['spots'][0]
        assert spot['verification_status'] == 'verified' and spot['amap_id'] == 'HOTEL001'
        assert logs[0]['action'] == 'db'
    engine.dispose()


def test_failed_specific_lookup_clears_guessed_location_and_cannot_mutate_input():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    trip = {'days': [{'spots': [{'name': '未找到的具体地点', 'lng': 120.1, 'lat': 30.2,
        'amap_id': 'fiction', 'address': 'fiction', 'verification_status': 'verified'}]}]}
    original = copy.deepcopy(trip)
    with Session(engine) as db:
        result, _ = map_verify.verify_itinerary_spots(trip, '杭州', db, amap_enabled=False)
    engine.dispose()
    spot = result['days'][0]['spots'][0]
    assert spot['verification_status'] == 'unverified' and spot['lng'] is None and spot['lat'] is None
    assert spot['amap_id'] == '' and trip == original


def test_conversion_preserves_unverified_meaning_and_original_history(client):
    c, sessions, auth, _, pid, body = setup(client)
    from app.db.models import AiPlan
    data = {'days': [{'day': 1, 'spots': [{'name': '附近餐馆', 'time': '12:00',
        'lng': 120.1, 'lat': 30.2, 'amap_id': 'guessed', 'address': 'guessed',
        'verification_status': 'unverified', 'note': '自行选择餐馆'}]}]}
    with sessions() as db:
        plan = db.get(AiPlan, pid)
        plan.result_json = json.dumps(data, ensure_ascii=False)
        db.commit()
    response = c.post(f'/api/trips/from-plan/{pid}/convert', headers=auth, json=body)
    assert response.status_code == 201, response.text
    item = response.json()['trip']['days'][0]['items'][0]
    assert item['name'] == '附近餐馆' and item['note'] == '自行选择餐馆' and item['start_time'] == '12:00'
    assert item['lng'] is None and item['lat'] is None and item['amap_id'] == '' and item['address'] == ''
    assert '地点待核实' in item['source']
    with sessions() as db:
        assert json.loads(db.get(AiPlan, pid).result_json) == data


@pytest.mark.parametrize('hit', [
    {'name': '具体地点', 'amap_id': 'A', 'lng': None, 'lat': None},
    {'name': '具体地点', 'amap_id': '', 'lng': 120.1, 'lat': 30.2},
    {'name': '具体地点', 'amap_id': 'A', 'lng': float('nan'), 'lat': 30.2},
    {'name': '具体地点', 'amap_id': 'A', 'lng': True, 'lat': 30.2},
])
def test_incomplete_or_invalid_provider_locations_are_not_reported_as_matched(monkeypatch, hit):
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    monkeypatch.setattr(map_verify, '_amap_search', lambda *args: [hit])
    monkeypatch.setattr(map_verify, 'REQUEST_INTERVAL', 0)
    with Session(engine) as db:
        result, logs = map_verify.verify_itinerary_spots({'days': [{'spots': [{'name': '具体地点'}]}]}, '杭州', db)
    engine.dispose()
    assert result['days'][0]['spots'][0]['verification_status'] == 'unverified'
    assert logs[0]['action'] == 'unverified'


def test_amap_search_skips_first_result_parking_and_other_city_exact_name(monkeypatch):
    requests = []
    def poi(name, ident, city='杭州市', **extras):
        return {'name': name, 'id': ident, 'cityname': city, 'location': '120.14,30.25', **extras}
    def mock(_url, **kwargs):
        requests.append(kwargs['params'])
        return httpx.Response(200, request=httpx.Request('GET', _url), json={'status': '1', 'pois': [
            poi('杭州博物馆-停车场', 'parking'),
            poi('杭州博物馆', 'other-city', city='宁波市'),
            poi('杭州市博物馆', 'museum', address='合成路1号'),
        ]})
    monkeypatch.setattr(map_verify, 'amap_get', mock)
    monkeypatch.setattr(map_verify.settings, 'amap_api_key', 'synthetic')
    monkeypatch.setattr(map_verify, 'REQUEST_INTERVAL', 0)
    engine = create_engine('sqlite:///:memory:')
    try:
        Spot.__table__.create(engine)
        with Session(engine) as db:
            result, logs = map_verify.verify_itinerary_spots({'days': [{'spots': [{'name': '杭州博物馆'}]}]}, '杭州', db)
    finally:
        engine.dispose()
    spot = result['days'][0]['spots'][0]
    assert spot['amap_id'] == 'museum' and spot['verification_status'] == 'verified'
    assert logs[0]['action'] == 'amap' and requests[0]['page_size'] > 1 and requests[0]['city_limit'] == 'true'


def test_bare_museum_retains_two_campuses_while_explicit_campus_resolves(monkeypatch):
    engine = create_engine('sqlite:///:memory:')
    try:
        Spot.__table__.create(engine)
        with Session(engine) as db:
            db.add_all([
                Spot(name='示例博物馆(北馆区)', city='杭州', lng=120.16, lat=30.21, amap_id='north', address='北馆地址'),
                Spot(name='示例博物馆(南馆区)', city='杭州', lng=120.13, lat=30.2, amap_id='south', address='南馆地址'),
            ])
            db.commit()
            result, _ = map_verify.verify_itinerary_spots({'days': [{'spots':[
                {'name':'示例博物馆', 'lng':120.09,'lat':30.01, 'district':'猜测', 'time':'10:00','cost_estimate':60},
                {'name':'示例博物馆（南馆区）', 'time':'13:00'},
            ]}]}, '杭州', db, amap_enabled=False)
    finally:
        engine.dispose()
    unresolved, resolved = result['days'][0]['spots']
    assert unresolved['name'] == '示例博物馆' and unresolved['time'] == '10:00' and unresolved['cost_estimate'] == 60
    assert unresolved['lng'] is None and unresolved['lat'] is None and unresolved['district'] == ''
    assert {row['address'] for row in unresolved['verification_candidates']} == {'北馆地址', '南馆地址'}
    assert all(set(row) == {'name','address','district'} for row in unresolved['verification_candidates'])
    assert resolved['amap_id'] == 'south' and resolved['verification_status'] == 'verified'


def test_graph_progress_counts_only_actual_matched_locations(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from app.db import database
    from app.services import graph
    closed = []
    monkeypatch.setattr(database, 'SessionLocal', lambda: SimpleNamespace(close=lambda: closed.append(True)))
    result = {'days': [{'spots': [
        {'name': '西湖', 'verification_status': 'verified'},
        {'name': '附近餐馆', 'verification_status': 'unverified'},
    ]}]}
    monkeypatch.setattr(map_verify, 'verify_itinerary_spots', lambda *args: (result, [
        {'spot': '西湖', 'action': 'amap'}, {'spot': '附近餐馆', 'action': 'unverified'},
    ]))
    response = asyncio.run(graph.verify_node({'collected': {'destination': '杭州'}, 'itinerary': {}, 'logs': []}))
    assert response['logs'] == ['地图核实：2 个安排中，1 个地点位置已匹配，1 个地点未能核实']
    assert closed == [True]
