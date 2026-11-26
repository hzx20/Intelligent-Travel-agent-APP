"""New generations use expense contributions, not model-provided arithmetic."""
import copy
import json

import pytest

from app.services import itinerary
from test_trip_ai_schema import run_immediate


def draft():
    return {'budget_format': 'items_and_additions_v1',
        'days': [{'day': 1, 'items': [{'poi': '公开场馆', 'cost_estimate': 50, 'cost_category': '门票'}]}],
        'additional_costs': [{'name': '2晚住宿', 'category': '住宿', 'amount': 600},
                             {'name': '市内通勤', 'category': '交通', 'amount': 120}]}


def validate(value, budget=3000):
    return itinerary._validated_itinerary(json.dumps(value), {'days': 1, 'budget': budget}, require_cost_components=True)


def test_categories_total_and_limit_come_from_unchanged_contributions():
    value = draft()
    before = copy.deepcopy(value)
    value['budget_summary'] = {'总计': 9999, '门票': 1}
    result = validate(value)
    assert result['budget_summary'] == {'交通': 120, '住宿': 600, '餐饮': 0, '门票': 50, '其他': 0, '总计': 770}
    assert result['days'][0]['items'][0]['cost_estimate'] == before['days'][0]['items'][0]['cost_estimate']
    assert result['additional_costs'] == before['additional_costs']
    assert result['budget_validation']['model_summary_ignored'] is True
    assert result['budget_validation']['quotes_verified'] is False


def test_unknown_items_and_additions_produce_only_a_known_subtotal():
    value = draft()
    value['days'][0]['items'][0]['cost_estimate'] = None
    value['additional_costs'][0]['amount'] = None
    result = validate(value)
    assert result['budget_summary']['总计'] == 120
    assert result['budget_validation']['unpriced_items'] == 2
    assert result['budget_validation']['unpriced_categories'] == ['住宿', '门票']
    assert result['budget_validation']['total_is_partial'] is True
    assert result['budget_validation']['item_costs_checked'] is False
    assert value['additional_costs'][0]['amount'] is None


def test_known_costs_over_cap_fail_even_when_model_claims_a_lower_total():
    value = draft()
    value['budget_summary'] = {'总计': 1}
    with pytest.raises(ValueError, match='已列费用合计为770元'):
        validate(value, 700)


@pytest.mark.parametrize('additional', [None, {}, [{}],
    [{'name': '住宿', 'category': '住宿', 'amount': True}],
    [{'name': '住宿', 'category': '住宿', 'amount': -1}],
    [{'name': '住宿', 'category': '错误类别', 'amount': 100}],
    [{'name': '', 'category': '住宿', 'amount': 100}],
])
def test_invalid_additional_expenses_fail_closed(additional):
    value = draft()
    value['additional_costs'] = additional
    with pytest.raises(ValueError):
        validate(value)


def test_decimal_expenses_are_summed_without_binary_rounding_mismatch():
    value = draft()
    value['days'][0]['items'][0]['cost_estimate'] = 0.1
    value['additional_costs'] = [{'name': '交通', 'category': '交通', 'amount': 0.2}]
    result = validate(value, '0.3')
    assert result['budget_summary']['总计'] == 0.3


def test_absent_category_is_distinguished_from_an_explicit_zero_estimate():
    value = draft()
    value['additional_costs'] = [{'name': '步行通勤', 'category': '交通', 'amount': 0}]
    result = validate(value)
    assert result['budget_summary']['交通'] == result['budget_summary']['住宿'] == 0
    assert result['budget_validation']['unlisted_categories'] == ['住宿', '餐饮', '其他']
    assert result['budget_summary']['总计'] == 50


def test_generation_rejects_old_aggregate_only_contract_then_accepts_components(monkeypatch):
    calls = []
    async def model(*args, **kwargs):
        calls.append(1)
        value = draft()
        if len(calls) == 1:
            del value['budget_format']
            value['budget_summary'] = {'总计': 770}
        return json.dumps(value)
    monkeypatch.setattr(itinerary, 'chat', model)
    result = run_immediate(itinerary.generate_itinerary({'days': 1, 'budget': 3000}))
    assert len(calls) == 2 and result['budget_summary']['总计'] == 770
    assert result['budget_validation']['calculation_basis'] == 'items_and_additions_v1'
