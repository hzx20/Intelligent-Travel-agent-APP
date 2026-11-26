"""Walking preferences carried from a plan into an editable trip."""
from decimal import Decimal
import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field


class WalkingPreferences(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')

    prefer_short_walks: bool = False
    check_climbs: bool = False
    daily_limit_m: int | None = Field(default=None, ge=1, le=100000)


_LIMIT = re.compile(
    r'(?:每天|每日)(?:的)?(?:地点间)?(?:累计)?步行(?:距离)?\s*(?:不超过|最多|上限(?:为)?|≤|<=)\s*'
    r'([0-9]{1,6}(?:\.[0-9]{1,3})?)\s*(公里|千米|km|米|m)(?![a-z])',
    re.IGNORECASE,
)
_SHORT_WALKS = re.compile(
    r'(?:^|[\s，。；、])(?:尽量)?(?:少走路|少步行)|(?:避免|不想|不要|别)'
    r'(?:爬山(?:和|及|、))?(?:长距离步行|走太多路|走太远)'
)
_CLIMBS = re.compile(r'(?:避免|不想|不要|别)(?:爬山|爬坡|走台阶|走楼梯)|(?:^|[\s，。；、])(?:尽量)?少(?:爬坡|爬山)')


def from_planning(value):
    if not isinstance(value, str):
        return {}
    value = unicodedata.normalize('NFKC', value).translate(str.maketrans({',':'，',';':'；'}))
    data = WalkingPreferences(prefer_short_walks=bool(_SHORT_WALKS.search(value)), check_climbs=bool(_CLIMBS.search(value)))
    # Preserve an explicit numeric maximum. Qualitative requests never become invented numbers.
    limits = []
    for amount, unit in _LIMIT.findall(value):
        meters = Decimal(amount) * (1000 if unit.lower() in ('公里', '千米', 'km') else 1)
        if meters == meters.to_integral_value() and 1 <= meters <= 100000:
            limits.append(int(meters))
    if limits:
        data.daily_limit_m = limits[-1]
    return data.model_dump(exclude_defaults=True)
