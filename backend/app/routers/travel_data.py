"""Authenticated, explicit weather/flight queries; never exposes supplier credentials."""
from threading import RLock

from cachetools import TTLCache
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.core.config import settings
from app.routers.auth import get_current_user
from app.services import qweather, variflight
from app.services.supplier_http import SupplierError

router = APIRouter(prefix="/api/travel-data", tags=["旅行天气与航班"],
                   dependencies=[Depends(get_current_user)])
_counts = TTLCache(maxsize=4096, ttl=60)
_lock = RLock()


def _limit(user_id, kind):
    # Bounded per-worker abuse guard; optional shared provider budgets also apply.
    key = (user_id, kind)
    with _lock:
        count = _counts.get(key, [0])[0]
        if count >= 6:
            raise HTTPException(429, "一分钟内最多查询 6 次，请稍后再试")
        # Keep the original window; TTLCache assignment would extend it.
        if key not in _counts:
            _counts[key] = [1]
        else:
            _counts[key][0] += 1


def _invoke(function, *args):
    try:
        return function(*args)
    except SupplierError as error:
        raise HTTPException(error.status_code, str(error)) from None


@router.get("/status")
def supplier_status(response: Response):
    response.headers["Cache-Control"] = "no-store"
    try:
        qweather.api_host()
        valid_host = True
    except SupplierError:
        valid_host = False
    return {"qweather": {"configured": bool(settings.qweather_api_key) and valid_host},
            "variflight": {"configured": bool(settings.variflight_api_key)}}


@router.get("/weather")
def weather_query(response: Response, city: str = Query(min_length=1, max_length=80),
                  user=Depends(get_current_user)):
    response.headers["Cache-Control"] = "no-store"
    if not city.strip():
        raise HTTPException(422, "请填写城市名称")
    _limit(user.id, "weather")
    return _invoke(qweather.fetch_weather, city.strip())


@router.post("/flights/query")
def flight_query(body: variflight.FlightQuery, response: Response, user=Depends(get_current_user)):
    response.headers["Cache-Control"] = "no-store"
    _limit(user.id, "flights")
    return _invoke(variflight.query_flights, body)
