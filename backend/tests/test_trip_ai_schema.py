"""Pure AI schema/simulation checks: no HTTP client, provider or business DB."""
import copy
import json

import pytest

from app.services import trip_ai


@pytest.fixture(autouse=True)
def forbid_provider_calls(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError('No provider call is allowed in this test module')
    monkeypatch.setattr(trip_ai, 'chat', forbidden)


def snapshot():
    return {'title': '公开样例旅行', 'days': [
        {'id': 1, 'day_number': 1, 'items': [
            {'id': 1, 'name': '西湖', 'note': '原备注', 'start_time': '', 'duration_min': 60,
             'lng': 120.14, 'lat': 30.25, 'source': '已核对地点'}]},
        {'id': 2, 'day_number': 2, 'items': [{'id': 2, 'name': '公园', 'note': '第二天原安排'}]},
    ], 'reservations': [{'reference': 'synthetic-local-record'}]}


def run_immediate(coroutine):
    """All mocked awaits complete immediately; an I/O event loop is unnecessary."""
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    finally:
        coroutine.close()
    raise AssertionError('A pure schema test unexpectedly attempted asynchronous I/O')


BAD_OPERATIONS = [
    *[{'action': 'remove', 'item_id': value} for value in (True, False, 1.0, '1')],
    *[{'action': 'edit', 'item_id': value, 'changes': {'note': '不能落地'}} for value in (True, 1.0, '1')],
    {'action': 'move', 'item_id': True, 'day_id': 2, 'index': 0},
    *[{'action': 'move', 'item_id': 1, 'day_id': value, 'index': 0} for value in (True, 2.0, '2')],
    *[{'action': 'move', 'item_id': 1, 'day_id': 2, 'index': value} for value in (False, 0.0, '0')],
    {'action': 'add', 'day_id': True, 'index': 0, 'item': {'name': '新地点'}},
    {'action': 'add', 'day_id': 1, 'index': False, 'item': {'name': '新地点'}},
    *[{'action': 'add', 'day_id': 1, 'index': 0, 'item': {'name': '新地点', 'duration_min': value}}
      for value in (True, 60.0, '60')],
    *[{'action': 'edit', 'item_id': 1, 'changes': {'duration_min': value}} for value in (True, 60.0, '60')],
]


@pytest.mark.parametrize('invalid', BAD_OPERATIONS)
def test_coerced_identifiers_positions_and_minutes_are_rejected_without_mutation(invalid):
    original = snapshot()
    before = copy.deepcopy(original)
    with pytest.raises(ValueError):
        trip_ai.simulate(original, [
            {'action': 'edit', 'item_id': 1, 'changes': {'note': '前置修改也不能落地'}}, invalid,
        ])
    assert original == before


def test_complex_valid_operations_keep_coordinates_records_and_zero_or_null_minutes():
    original = snapshot()
    before = copy.deepcopy(original)
    operations = [
        {'action': 'edit', 'item_id': 1, 'changes': {'duration_min': None, 'note': '核对后的备注'}},
        {'action': 'move', 'item_id': 1, 'day_id': 2, 'index': 1},
        {'action': 'remove', 'item_id': 2},
        {'action': 'add', 'day_id': 1, 'index': 0, 'item': {'name': '自由活动', 'duration_min': 0}},
    ]
    result, changes = trip_ai.simulate(original, operations)
    assert original == before
    assert [c['action'] for c in changes] == ['edit', 'move', 'remove', 'add']
    assert result['days'][0]['items'][0]['duration_min'] == 0
    moved = result['days'][1]['items'][0]
    assert moved['id'] == 1 and moved['lng'] == 120.14 and moved['lat'] == 30.25
    assert moved['duration_min'] is None and moved['source'] == '已核对地点'
    assert result['reservations'] == before['reservations']


def test_invalid_model_identifier_uses_one_retry_and_never_returns_wrong_delete(monkeypatch):
    original = snapshot()
    before = copy.deepcopy(original)
    calls = []
    async def reply(messages, **kwargs):
        calls.append(messages)
        operations = [{'action': 'remove', 'item_id': True}] if len(calls) == 1 else [
            {'action': 'edit', 'item_id': 1, 'changes': {'note': '有效建议'}},
        ]
        return json.dumps({'operations': operations})
    monkeypatch.setattr(trip_ai, 'chat', reply)
    operations, changes = run_immediate(trip_ai.propose(original, '调整第一天'))
    assert len(calls) == 2 and operations[0]['action'] == 'edit'
    assert changes[0]['name'] == '西湖' and original == before
    assert '上一次输出无法通过' in calls[1][1]['content']


def test_two_invalid_model_responses_fail_closed_without_extra_calls(monkeypatch):
    original = snapshot()
    before = copy.deepcopy(original)
    calls = []
    async def reply(*args, **kwargs):
        calls.append(1)
        return '{"operations":[{"action":"remove","item_id":true}]}'
    monkeypatch.setattr(trip_ai, 'chat', reply)
    with pytest.raises(ValueError, match='无法通过校验'):
        run_immediate(trip_ai.propose(original, '调整第一天'))
    assert len(calls) == 2 and original == before
