"""Keep the traveler's schedule requirements through generation and conversion."""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.models import AiPlan, Spot
from app.routers.trips import _trip_from_plan
from app.services import clarify, graph, itinerary, map_verify
from app.services.plan_schedule import schedule_requirements, trip_start_date
from test_trip_ai_schema import run_immediate
from test_trips import account
from test_v13_jobs import _wait_job


INTENT = {
    'destination': '杭州', 'days': 3, 'budget': 3000, 'dates': '2026年11月21日至23日',
    'preferences': '带5岁儿童，避免爬山和长距离步行；第二天以室内场馆为主；每天保留午餐和休息，不跨城市。',
}


def plan_with_schedule():
    days = []
    for day in range(1, 4):
        days.append({'day': day, 'items': [
            {'type': '室内场馆' if day == 2 else '景点', 'poi': '浙江省博物馆之江馆' if day == 2 else '西湖',
             'time': '09:00', 'cost_estimate': None, 'cost_category': '门票'},
            {'type': '餐饮', 'poi': '附近餐馆', 'time': '12:00', 'duration_min': 45,
             'cost_estimate': 150, 'cost_category': '餐饮'},
            {'type': '休息', 'poi': '', 'activity': '午休', 'time': '13:00', 'duration_min': 60,
             'cost_estimate': 0, 'cost_category': '其他'},
            {'type': '室内场馆' if day == 2 else '景点', 'poi': '浙江自然博物院杭州馆' if day == 2 else '良渚古城遗址公园',
             'time': '15:00', 'cost_estimate': None, 'cost_category': '门票'},
        ]})
    return {'days': days, 'budget_format': 'items_and_additions_v1',
            'additional_costs': [{'name': '住宿', 'category': '住宿', 'amount': None}]}


@pytest.mark.parametrize(('value', 'days', 'expected'), [
    ('2026年11月21日至23日', 3, '2026-11-21'),
    ('2026-11-21', 3, '2026-11-21'),
    ('2026-12-31 — 2027-01-02', 3, '2026-12-31'),
    ('2026年11月30日至12月2日', 3, '2026-11-30'),
    ('11月21日至23日', 3, None),
    ('2026年11月21日或22日', 3, None),
    ('2026年2月30日', 3, None),
    ('2026年11月21日至24日', 3, None),
])
def test_trip_date_requires_explicit_valid_start_and_consistent_range(value, days, expected):
    assert trip_start_date(value, days) == expected


def test_requirements_preserve_scope_and_do_not_apply_negated_requests():
    assert schedule_requirements(INTENT, 3) == {
        'daily_rest': 'midday', 'daily_lunch': True, 'indoor_majority_days': [2]}
    assert schedule_requirements({'preferences': '不用每天午休；第二天不用以室内为主；第二十二天以室内为主'}, 3) == {}
    assert schedule_requirements({'preferences': '第2天以室内为主，第十二天以室内为主；每天留时间休息'}, 12) == {
        'daily_rest': 'any', 'indoor_majority_days': [2, 12]}


@pytest.mark.parametrize(('case', 'error'), [
    ('no_rest', '第1天缺少独立的休息时段'),
    ('no_lunch', '第1天缺少11:00至14:00之间的午餐安排'),
    ('late_rest', '第1天须在11:00至15:00之间开始午间休息'),
    ('overlap', '且不与前后安排重叠'),
    ('previous_overlap', '且不与前后安排重叠'),
    ('rest_as_place', '不填地图地点'),
    ('repeat_indoor', '第2天须以室内场馆为主'),
    ('mostly_outdoors', '第2天须以室内场馆为主'),
    ('outdoor_as_indoor', '明显包含户外游览'),
])
def test_preference_in_reason_does_not_replace_real_arrangements(case, error):
    value = plan_with_schedule()
    day1, day2, _ = value['days']
    day1['reason'] = '已经为带娃家庭预留了全部午餐和休息'
    if case == 'no_rest':
        del day1['items'][2]
    elif case == 'no_lunch':
        day1['items'][1]['time'] = '10:30'
    elif case == 'late_rest':
        day1['items'].append(day1['items'].pop(2))
        day1['items'][-1]['time'] = '16:00'
    elif case == 'overlap':
        day1['items'][2]['duration_min'] = 150
    elif case == 'previous_overlap':
        day1['items'][1]['duration_min'] = 75
    elif case == 'rest_as_place':
        day1['items'][2]['poi'] = '某休息室'
    elif case == 'repeat_indoor':
        day2['items'][3]['poi'] = day2['items'][0]['poi']
    elif case == 'outdoor_as_indoor':
        day2['items'][3]['poi'] = '杭州动物园'
    else:
        day2['items'][3]['type'] = '景点'
    with pytest.raises(ValueError, match=error):
        itinerary._validated_itinerary(json.dumps(value), INTENT, require_cost_components=True)


def test_generation_retries_and_keeps_rest_through_map_and_trip_conversion(monkeypatch):
    calls = []

    async def model(messages, **kwargs):
        calls.append(messages[-1]['content'])
        if len(calls) == 2:
            return json.dumps({'days': [{'day': 1, 'changes': [
                {'op': 'add_rest', 'time': '13:00', 'duration_min': 60}
            ]}]})
        response = plan_with_schedule()
        response['schedule_requirements'] = {'daily_rest': 'invented'}
        response['days'][1]['spots'] = [{'name': '不应接受模型另列的地点'}]
        response['days'][0]['items'].pop(2)
        return json.dumps(response, ensure_ascii=False)

    monkeypatch.setattr(itinerary, 'chat', model)
    result = run_immediate(itinerary.generate_itinerary(INTENT))
    assert len(calls) == 2 and '第1天缺少独立的休息时段' in calls[1]
    context = json.loads(calls[1])
    assert list(context) == ['requirements', 'days'] and [day['day'] for day in context['days']] == [1]
    assert '【必须落实的日程要求】' in calls[0]
    assert result['schedule_requirements'] == schedule_requirements(INTENT, 3)
    assert all(len(day['spots']) == 3 for day in result['days'])
    assert result['days'][1]['spots'][0]['name'] == '浙江省博物馆之江馆'
    assert result['additional_costs'] == [{'name': '住宿', 'category': '住宿', 'amount': None}]
    assert result['budget_summary']['餐饮'] == 450

    engine = create_engine('sqlite:///:memory:')
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            db.add(Spot(city='杭州', name='西湖', lng=120.1, lat=30.2, amap_id='outdoor-alternative'))
            db.commit()
            verified, _ = map_verify.verify_itinerary_spots(result, '杭州', db, amap_enabled=False)
    finally:
        engine.dispose()
    assert verified['days'][1]['spots'][0]['name'] == '浙江省博物馆之江馆'
    assert verified['days'][1]['spots'][0]['verification_status'] == 'unverified'
    raw = json.dumps(verified, ensure_ascii=False)
    history = AiPlan(id=123, user_id=1, result_json=raw, intent_json=json.dumps(INTENT))
    trip = _trip_from_plan(history)
    assert trip.start_date == '2026-11-21'
    for day in trip.days:
        rest = next(item for item in day.items if item.kind == '休息')
        assert (rest.name, rest.start_time, rest.duration_min, rest.lng, rest.lat) == ('午休', '13:00', 60, None, None)
        assert day.items[2] is rest
    assert history.result_json == raw


@pytest.mark.parametrize('changes', [
    [{'op': 'add_rest', 'time': '12:30', 'duration_min': 60}],
    [{'op': 'add_rest', 'time': '16:00', 'duration_min': 60}],
    [{'op': 'add_rest', 'time': '13:00', 'duration_min': 60, 'cost_estimate': 0}],
    [{'op': 'move', 'item': True, 'time': '13:00'}],
    [{'op': 'add_rest', 'time': '13:00', 'duration_min': 60},
     {'op': 'move', 'item': 3, 'time': '08:00'}],
])
def test_invalid_correction_never_publishes_or_retries_a_third_time(changes, monkeypatch):
    draft = plan_with_schedule()
    draft['days'][0]['items'].pop(2)
    replies = iter((draft, {'days': [{'day': 1, 'changes': changes}]}))

    async def model(*args, **kwargs):
        return json.dumps(next(replies))

    monkeypatch.setattr(itinerary, 'chat', model)
    with pytest.raises(RuntimeError, match='行程结构校验失败'):
        run_immediate(itinerary.generate_itinerary(INTENT))


def test_late_rest_can_move_without_dropping_its_estimated_cost(monkeypatch):
    draft = plan_with_schedule()
    items = draft['days'][0]['items']
    rest = items.pop(2)
    rest.update(time='16:00', cost_estimate=25)
    items.append(rest)
    replies = iter((draft, {'days': [{'day': 1, 'changes': [
        {'op': 'set_rest', 'item': 4, 'time': '13:00', 'duration_min': 60}
    ]}]}))

    async def model(*args, **kwargs):
        return json.dumps(next(replies))

    monkeypatch.setattr(itinerary, 'chat', model)
    result = run_immediate(itinerary.generate_itinerary(INTENT))
    corrected = result['days'][0]['items'][2]
    assert corrected['time'] == '13:00'
    assert corrected['duration_min'] == 60 and corrected['cost_estimate'] == 25
    assert result['budget_summary']['其他'] == 25


@pytest.mark.parametrize('specified', [False, True])
def test_lunch_gap_reserves_known_transfer_and_respects_explicit_rest_timing(specified, monkeypatch):
    draft = plan_with_schedule()
    items = draft['days'][0]['items']
    items.pop(2)
    items[1]['access'] = {'mode': '步行', 'min': 30}
    intent = {**INTENT}
    if specified:
        intent['preferences'] += '午休要60分钟。'
    requests = []

    async def model(messages, **kwargs):
        requests.append(messages)
        if len(requests) == 1:
            return json.dumps(draft)
        return json.dumps({'days': [{'day': 1, 'changes': [
            {'op': 'add_rest', 'time': '13:15', 'duration_min': 60},
        ]}]})

    monkeypatch.setattr(itinerary, 'chat', model)
    result = run_immediate(itinerary.generate_itinerary(intent))
    rest = result['days'][0]['items'][2]
    assert rest['type'] == '休息' and rest['time'] == '13:15'
    assert rest['duration_min'] == (60 if specified else 30)
    assert len(requests) == (2 if specified else 1)
    if specified:
        assert json.loads(requests[1][-1]['content'])['rest_timing_preference'] == '午休要60分钟'
    assert result['days'][0]['spots'][2]['name'] == '良渚古城遗址公园'
    assert result['budget_summary']['餐饮'] == 450


@pytest.mark.parametrize('amount', [None, 0, 25])
def test_rest_category_alias_preserves_amount_and_incomplete_budget(amount):
    value = plan_with_schedule()
    value['days'][0]['items'][2].update(cost_category='休息', cost_estimate=amount)
    result = itinerary._validated_itinerary(json.dumps(value), INTENT, require_cost_components=True)
    rest = result['days'][0]['items'][2]
    assert rest['cost_category'] == '其他' and rest['cost_estimate'] == amount
    assert result['budget_summary']['其他'] == (amount or 0)
    assert ('其他' in result['budget_validation']['unpriced_categories']) is (amount is None)


def test_retry_reports_late_rest_and_separate_invalid_cost_together():
    value = plan_with_schedule()
    first_day = value['days'][0]
    rest = first_day['items'].pop(2)
    rest.update(time='16:00', cost_category='休息', cost_estimate=None)
    first_day['items'].append(rest)
    first_day['items'][1]['cost_category'] = '休息'
    with pytest.raises(ValueError) as error:
        itinerary._validated_itinerary(json.dumps(value), INTENT, require_cost_components=True)
    message = str(error.value)
    assert '第1天须在11:00至15:00之间开始午间休息' in message
    assert '第1天第2项费用类别' in message


def test_job_saves_recovered_schedule_and_conversion_reads_the_same_rest_slots(client, monkeypatch):
    from app.db import database

    c, sessions = client
    owner = account(c, 'schedule_job_owner')
    calls = []

    async def collect(messages, **kwargs):
        assert INTENT['preferences'] in messages[-1]['content']
        return json.dumps({'collected': INTENT})

    async def model(messages, **kwargs):
        calls.append(messages[-1]['content'])
        value = plan_with_schedule()
        for day in value['days']:
            day['items'][1].update(poi=' ', activity='午餐', lng=120.1, lat=30.2, amap_id='猜测')
        if len(calls) == 1:
            value['days'][1]['items'][0]['type'] = '景点'
            value['days'][1]['items'][3]['type'] = '景点'
        return json.dumps(value)

    locate = map_verify.verify_itinerary_spots
    monkeypatch.setattr(clarify, 'chat', collect)
    monkeypatch.setattr(itinerary, 'chat', model)
    monkeypatch.setattr(database, 'SessionLocal', sessions)
    monkeypatch.setattr(map_verify, 'verify_itinerary_spots', lambda plan, city, db, enabled: locate(plan, city, db, False))
    monkeypatch.setattr(graph.weather, 'fetch_weather', lambda *_: {'ok': False})

    response = c.post('/api/plan/jobs', headers=owner, json={
        'history': [], 'user_input': '杭州三天，' + INTENT['preferences'], 'prev_collected': {}})
    assert response.status_code == 200, response.text
    final, _ = _wait_job(c, response.json()['job_id'], headers=owner)
    assert final['status'] == 'done', final
    assert len(calls) == 2 and '第2天须以室内场馆为主' in calls[1]
    plan_id = final['result']['plan_id']
    read = c.get(f'/api/plan/{plan_id}', headers=owner)
    assert read.status_code == 200, read.text
    snapshot = read.json()
    assert snapshot['itinerary']['schedule_requirements'] == schedule_requirements(INTENT, 3)
    assert len([item for day in snapshot['itinerary']['days'] for item in day['items'] if item['type'] == '休息']) == 3
    conversion = c.post(f'/api/trips/from-plan/{plan_id}/convert', headers=owner, json={
        'version': snapshot['version'], 'revision': snapshot['revision']})
    assert conversion.status_code == 201, conversion.text
    for day in conversion.json()['trip']['days']:
        rest = [item for item in day['items'] if item['kind'] == '休息']
        assert len(rest) == 1 and rest[0]['start_time'] == '13:00' and rest[0]['duration_min'] == 60
        meal = [item for item in day['items'] if item['kind'] == '餐饮']
        assert len(meal) == 1
        assert (meal[0]['name'], meal[0]['start_time'], meal[0]['duration_min']) == ('午餐', '12:00', 45)
        assert meal[0]['lng'] is None and meal[0]['lat'] is None and meal[0]['amap_id'] == ''
