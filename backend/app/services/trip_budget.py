"""Keep the original estimate separate from the user's quote checklist."""
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


MAX_MINOR = 100_000_000_000
EstimateCategory = Literal['交通', '住宿', '餐饮', '门票', '其他']
QuoteCategory = Literal['local_transport', 'intercity_transport', 'lodging', 'food', 'admission', 'other']
ESTIMATE_CATEGORIES = ('交通', '住宿', '餐饮', '门票', '其他')


class QuoteEntry(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    status: Literal['quoted', 'excluded']
    amount_minor: int | None = Field(default=None, ge=0, le=MAX_MINOR, strict=True)
    source: str = Field(min_length=1, max_length=200)
    checked_on: date | None = None

    @model_validator(mode='after')
    def check_evidence(self):
        if self.status == 'quoted' and (self.amount_minor is None or self.checked_on is None):
            raise ValueError('填写报价时需要金额、来源和核对日期')
        if self.status == 'excluded' and (self.amount_minor is not None or self.checked_on is not None):
            raise ValueError('不计入预算的项目只填写原因')
        return self


class BudgetChecklist(BaseModel):
    model_config = ConfigDict(extra='forbid')
    limit_minor: int | None = Field(ge=0, le=MAX_MINOR, strict=True)
    entries: dict[QuoteCategory, QuoteEntry] = Field(default_factory=dict, max_length=6)


def _minor(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)) or len(str(value)) > 64:
        return None
    try:
        amount = Decimal(str(value).strip())
        if not amount.is_finite() or amount < 0 or amount > MAX_MINOR // 100:
            return None
        minor = amount * 100
        return int(minor) if minor == minor.to_integral_value() else None
    except InvalidOperation:
        return None


def from_plan(intent, result):
    reference = {}
    limit = _minor(intent.get('budget'))
    if limit is not None:
        reference['limit_minor'] = limit
    validation, summary = result.get('budget_validation'), result.get('budget_summary')
    if (isinstance(validation, dict) and isinstance(summary, dict)
            and validation.get('summary_checked') is True
            and validation.get('calculation_basis') == 'items_and_additions_v1'):
        subtotal = _minor(summary.get('总计'))
        unknown = validation.get('unpriced_items')
        unlisted = validation.get('unlisted_categories')
        if subtotal is not None and type(unknown) is int and 0 <= unknown <= 10000 and isinstance(unlisted, list):
            reference.update(subtotal_minor=subtotal, unpriced_items=unknown,
                             unlisted_categories=[name for name in ESTIMATE_CATEGORIES if name in unlisted])
    return {'reference': reference} if reference else {}
