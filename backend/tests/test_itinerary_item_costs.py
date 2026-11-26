"""Line-item estimates must fit category totals without inventing unknown costs."""
import copy
import json

import pytest

from app.services import itinerary
from test_trip_ai_schema import run_immediate


def plan(items, budget):
    return {'days': [{'day': 1, 'items': items}], 'budget_summary': budget}


def budget(tickets=150, other=0):
    return {'交通': 120, '住宿': 600, '餐饮': 300, '门票': tickets, '其他': other,
            '总计': 1020 + tickets + other}


def visits():
    return [{'poi': f'公开场馆{index}', 'time': f'{index+8}:00', 'type': '室内场馆',
             'cost_estimate': 50} for index in range(3)]


def test_real_three_fifty_yuan_items_cannot_fit_a_fifty_yuan_ticket_summary():
    value = plan(visits(), budget(50))
    original = copy.deepcopy(value)
    with pytest.raises(ValueError, match='门票已列明细合计为150元，超过该类汇总50元'):
        itinerary._validated_itinerary(json.dumps(value), {'days': 1, 'budget': 3000})
    assert value == original


def test_explicit_categories_fit_totals_and_do_not_claim_real_quotes():
    items = [{**item, 'cost_category': '门票'} for item in visits()]
    value = itinerary._validated_itinerary(json.dumps(plan(items, budget())), {'days': 1})
    assert value['budget_validation'] == {'source': 'ai_estimate', 'summary_checked': True,
        'item_costs_checked': True, 'quotes_verified': False, 'warnings': []}
    assert value['budget_summary'] == budget()


def test_missing_and_null_costs_remain_unknown_instead_of_becoming_zero():
    items = [{'poi': '休息', 'cost_estimate': None}, {'poi': '公开地点'}]
    value = itinerary._validated_itinerary(json.dumps(plan(items, budget())), {'days': 1})
    assert value['days'][0]['items'][0]['cost_estimate'] is None
    assert 'cost_estimate' not in value['days'][0]['items'][1]
    assert value['budget_validation']['item_costs_checked'] is False
    assert '有2项安排未给出费用' in value['budget_validation']['warnings'][0]


@pytest.mark.parametrize('amount', [True, -1, 'NaN', float('inf')])
def test_invalid_item_money_is_not_normalized_into_a_valid_estimate(amount):
    with pytest.raises(ValueError, match='费用必须为有限非负数字'):
        itinerary._validated_itinerary(json.dumps(plan([
            {'poi': '公开地点', 'cost_estimate': amount, 'cost_category': '门票'}], budget())), {'days': 1})


def test_unknown_positive_cost_needs_an_explicit_category_and_other_is_in_total():
    item = {'poi': '公开活动', 'type': '体验项目', 'cost_estimate': 25}
    with pytest.raises(ValueError, match='缺少明确cost_category'):
        itinerary._validated_itinerary(json.dumps(plan([item], budget())), {'days': 1})
    value = itinerary._validated_itinerary(json.dumps(plan([
        {**item, 'cost_category': '其他'}], budget(other=25))), {'days': 1})
    assert value['budget_validation']['item_costs_checked'] is True


def test_legacy_total_only_cannot_hide_items_exceeding_total():
    with pytest.raises(ValueError, match='已列安排费用合计为150元，超过预算总计100元'):
        itinerary._validated_itinerary(json.dumps(plan(visits(), {'总计': 100})), {'days': 1})


def test_retry_preserves_item_values_and_updates_consistent_summary(monkeypatch):
    calls = []
    async def model(messages, **kwargs):
        calls.append(messages)
        items = visits() if len(calls) == 1 else [{**item, 'cost_category': '门票'} for item in visits()]
        value = plan(items, budget(50))
        value.update(budget_format='items_and_additions_v1', additional_costs=[])
        return json.dumps(value)
    monkeypatch.setattr(itinerary, 'chat', model)
    value = run_immediate(itinerary.generate_itinerary({'days': 1, 'budget': 3000}))
    assert '缺少cost_category' in calls[1][1]['content'] and len(calls) == 2
    assert [i['cost_estimate'] for i in value['days'][0]['items']] == [50, 50, 50]
    assert value['budget_summary']['门票'] == 150


def test_model_cannot_mark_its_own_estimates_as_verified_supplier_quotes():
    value = plan([{**visits()[0], 'cost_category': '门票'}], budget())
    value['budget_validation'] = {'quotes_verified': True, 'source': 'supplier'}
    result = itinerary._validated_itinerary(json.dumps(value), {'days': 1})
    assert result['budget_validation']['source'] == 'ai_estimate'
    assert result['budget_validation']['quotes_verified'] is False
