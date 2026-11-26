"""Reject real-planner arithmetic/limit errors before publishing a result."""
import copy
import json

import pytest

from app.services import itinerary
from test_trip_ai_schema import run_immediate


def reply(budget=None, numbers=(1, 2, 3)):
    result = {'days': [{'day': day, 'items': [{'poi': '公开地点', 'time': '09:00', 'cost_category': '其他'}]}
                       for day in numbers]}
    if budget is not None:
        result['budget_summary'] = budget
    return json.dumps(result, ensure_ascii=False)


def test_real_failed_reply_reports_both_arithmetic_and_cap_without_mutating_claim():
    budget = {'交通': 300, '住宿': 1500, '餐饮': 800, '门票': 650, '总计': 3650}
    before = copy.deepcopy(budget)
    with pytest.raises(ValueError) as error:
        itinerary._validated_itinerary(reply(budget), {'days': 3, 'budget': 3000})
    assert '3250' in str(error.value) and '3650' in str(error.value) and '3000' in str(error.value)
    assert budget == before


@pytest.mark.parametrize('numbers', [(1, 1, 3), (2, 1, 3), (True, 2, 3), ('1', 2, 3), (1.0, 2, 3)])
def test_supplied_daily_numbers_must_be_ordered_unique_json_integers(numbers):
    with pytest.raises(ValueError, match='编号必须是整数'):
        itinerary._validated_itinerary(reply(numbers=numbers), {'days': 3})


def test_missing_daily_numbers_receive_canonical_array_order():
    raw = {'days': [{'items': [{'poi': '公开地点甲'}]}, {'items': [{'poi': '公开地点乙'}]}]}
    result = itinerary._validated_itinerary(json.dumps(raw), {'days': 2})
    assert [day['day'] for day in result['days']] == [1, 2]
    assert [day['spots'][0]['name'] for day in result['days']] == ['公开地点甲', '公开地点乙']


def test_decimal_arithmetic_and_numeric_string_cap_are_exact():
    budget = {'交通': 0.1, '住宿': 0.2, '餐饮': 0, '门票': 0, '总计': 0.3}
    result = itinerary._validated_itinerary(reply(budget), {'days': 3, 'budget': '0.30'})
    assert result['budget_summary'] == budget


@pytest.mark.parametrize('budget', [
    {'交通': 100, '住宿': 100, '餐饮': 100, '门票': 100, '总计': 300},
    {'交通': 100, '住宿': 100, '餐饮': 100, '门票': 100, '总计': 400},
    {'交通': True, '住宿': 0, '餐饮': 0, '门票': 0, '总计': 1},
    {'交通': -1, '住宿': 0, '餐饮': 0, '门票': 0, '总计': 0},
    {'交通': 1, '总计': 1},
    {'总计': float('inf')}, {'总计': 'NaN'},
])
def test_inconsistent_over_limit_or_invalid_amounts_fail_closed(budget):
    with pytest.raises(ValueError):
        itinerary._validated_itinerary(reply(budget), {'days': 3, 'budget': 300})


def test_retry_receives_both_real_failures_then_validates_revised_plan(monkeypatch):
    invalid = {'交通': 300, '住宿': 1500, '餐饮': 800, '门票': 650, '总计': 3650}
    valid = {'交通': 200, '住宿': 1200, '餐饮': 800, '门票': 500, '总计': 2700}
    messages = []

    async def model(requests, **kwargs):
        messages.append(requests)
        amounts = invalid if len(messages) == 1 else valid
        value = json.loads(reply(amounts))
        value.update(budget_format='items_and_additions_v1', additional_costs=[
            {'name': name + '补充费用', 'category': name, 'amount': amounts[name]}
            for name in ('交通', '住宿', '餐饮', '门票')])
        return json.dumps(value)

    monkeypatch.setattr(itinerary, 'chat', model)
    result = run_immediate(itinerary.generate_itinerary({'days': 3, 'budget': 3000}))
    correction = messages[1][1]['content']
    assert '已列费用合计为3250' in correction and '上限为3000' in correction
    assert result['budget_summary'] == {**valid, '其他': 0} and len(messages) == 2


def test_persistent_invalid_budget_never_becomes_success_after_two_attempts(monkeypatch):
    calls = []
    async def model(*args, **kwargs):
        calls.append(1)
        value = json.loads(reply())
        value.update(budget_format='items_and_additions_v1', additional_costs=[
            {'name': '补充费用', 'category': '住宿', 'amount': 4000}])
        return json.dumps(value)
    monkeypatch.setattr(itinerary, 'chat', model)
    with pytest.raises(RuntimeError, match='上限为3000'):
        run_immediate(itinerary.generate_itinerary({'days': 3, 'budget': 3000}))
    assert len(calls) == 2
