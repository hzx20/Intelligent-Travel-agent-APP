"""Regression for real-model replies using pre-move insertion indices."""
import copy
import json

import pytest

from app.services import trip_ai
from test_trip_ai_schema import run_immediate, snapshot


def trip_with_two_first_day_items():
    trip = snapshot()
    trip['days'][0]['items'].append({**trip['days'][0]['items'][0], 'id': 3, 'name': '博物馆'})
    return trip


@pytest.mark.parametrize('kind,hint', [('add', '新增位置越界'), ('move', '移动位置越界')])
def test_position_retry_uses_current_list_and_preserves_original(monkeypatch, kind, hint):
    trip = trip_with_two_first_day_items()
    before = copy.deepcopy(trip)
    if kind == 'add':
        invalid = [
            {'action': 'move', 'item_id': 3, 'day_id': 2, 'index': 1},
            {'action': 'add', 'day_id': 1, 'index': 2, 'item': {'name': '休息', 'duration_min': 30}},
        ]
    else:
        invalid = [{'action': 'move', 'item_id': 1, 'day_id': 1, 'index': 2}]
    valid = copy.deepcopy(invalid)
    valid[-1]['index'] = 1
    calls = []

    async def reply(messages, **kwargs):
        calls.append(messages)
        return json.dumps({'operations': invalid if len(calls) == 1 else valid})

    monkeypatch.setattr(trip_ai, 'chat', reply)
    operations, _ = run_immediate(trip_ai.propose(trip, '调整顺序并保留其他内容'))
    assert operations == valid and len(calls) == 2
    assert hint in calls[1][1]['content'] and hint not in calls[0][1]['content']
    assert 'day_id=1' in calls[1][1]['content'] and '合法index为0到1' in calls[1][1]['content']
    assert '追加到末尾必须使用index=1' in calls[1][1]['content']
    assert ('第2步' if kind == 'add' else '第1步') in calls[1][1]['content']
    result, _ = trip_ai.simulate(trip, operations)
    assert trip == before and result['reservations'] == before['reservations']
    if kind == 'add':
        assert [i['id'] for i in result['days'][1]['items']] == [2, 3]
        assert [i['name'] for i in result['days'][0]['items']] == ['西湖', '休息']
        assert result['days'][0]['items'][0] == before['days'][0]['items'][0]
    else:
        assert [i['id'] for i in result['days'][0]['items']] == [3, 1]
        assert result['days'][0]['items'][1] == before['days'][0]['items'][0]


@pytest.mark.parametrize('kind', ['add', 'move'])
def test_repeated_out_of_range_position_is_rejected_without_clamping(monkeypatch, kind):
    trip = trip_with_two_first_day_items()
    before = copy.deepcopy(trip)
    invalid = ([{'action': 'move', 'item_id': 3, 'day_id': 2, 'index': 1},
                {'action': 'add', 'day_id': 1, 'index': 2, 'item': {'name': '休息'}}]
               if kind == 'add' else [{'action': 'move', 'item_id': 1, 'day_id': 1, 'index': 2}])
    calls = []

    async def reply(*args, **kwargs):
        calls.append(1)
        return json.dumps({'operations': invalid})

    monkeypatch.setattr(trip_ai, 'chat', reply)
    with pytest.raises(ValueError, match='无法通过校验'):
        run_immediate(trip_ai.propose(trip, '调整顺序'))
    assert len(calls) == 2 and trip == before


def test_schema_retry_does_not_echo_untrusted_validation_input(monkeypatch):
    calls = []

    async def reply(messages, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps({'operations': [{'action': 'remove', 'item_id': True,
                'unexpected': 'UNTRUSTED_MODEL_DIRECTIVE'}]})
        return '{"operations":[]}'

    monkeypatch.setattr(trip_ai, 'chat', reply)
    assert run_immediate(trip_ai.propose(snapshot(), '检查安排')) == ([], [])
    assert len(calls) == 2
    assert '上一次输出无法通过' in calls[1][1]['content']
    assert 'UNTRUSTED_MODEL_DIRECTIVE' not in calls[1][1]['content']
