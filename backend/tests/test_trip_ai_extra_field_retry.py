"""Real-model regression: copying source/location fields into a new AI item."""
import copy
import json

import pytest

from app.services import trip_ai
from test_trip_ai_schema import run_immediate, snapshot


@pytest.mark.parametrize('extra', [
    {'source': '自定义'}, {'lng': 120.14, 'lat': 30.25}, {'address': '原地点地址'},
])
def test_extra_new_item_fields_are_explained_then_removed_by_model(monkeypatch, extra):
    trip = snapshot()
    original = copy.deepcopy(trip)
    item = {'name': '休息', 'kind': '活动', 'duration_min': 30}
    calls = []

    async def reply(messages, **kwargs):
        calls.append(messages)
        return json.dumps({'operations': [{'action': 'add', 'day_id': 1, 'index': 1,
            'item': {**item, **extra} if len(calls) == 1 else item}]})

    monkeypatch.setattr(trip_ai, 'chat', reply)
    operations, _ = run_immediate(trip_ai.propose(trip, '原安排后增加休息'))
    assert len(calls) == 2 and operations[0]['item'] == item
    assert '输出包含格式以外的字段' in calls[1][1]['content']
    assert '不要输出source、address、lng、lat或amap_id' in calls[1][1]['content']
    result, _ = trip_ai.simulate(trip, operations)
    added = result['days'][0]['items'][1]
    assert added['source'] == 'AI 建议（地点待核实）'
    assert added['lng'] is None and added['lat'] is None and added['address'] == ''
    assert trip == original


def test_two_extra_field_replies_are_not_silently_stripped_or_accepted(monkeypatch):
    trip = snapshot()
    original = copy.deepcopy(trip)
    calls = []

    async def reply(*args, **kwargs):
        calls.append(1)
        return '{"operations":[{"action":"add","day_id":1,"index":1,"item":{"name":"休息","source":"已核实"}}]}'

    monkeypatch.setattr(trip_ai, 'chat', reply)
    with pytest.raises(ValueError, match='无法通过校验'):
        run_immediate(trip_ai.propose(trip, '增加休息'))
    assert len(calls) == 2 and trip == original
