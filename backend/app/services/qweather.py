"""QWeather GeoAPI + weather/v1 daily forecast, adapted to the itinerary contract."""
from copy import deepcopy
from datetime import datetime
import hashlib
import math
import re
from threading import RLock

from cachetools import TTLCache
import httpx

from app.core.config import settings
from app.core.provider_budget import ProviderBusy, provider_slot
from app.core.shared_cache import CacheUnavailable, provider_cache
from app.services.supplier_http import SupplierError, request_json

_FORECAST_TTL = 900
_cache = TTLCache(maxsize=256, ttl=_FORECAST_TTL)
_lock = RLock()


def api_host():
    host = settings.qweather_api_host.strip().lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\.qweatherapi\.com", host):
        raise SupplierError("请在服务端配置和风天气控制台的专属 API Host（仅域名）", 503)
    return host


def _city_key(name):
    return str(name).strip().removesuffix("市")


def _fetch(city, client, host, key):
    headers = {"X-QW-Api-Key": key}
    geo = request_json(client, "GET", f"https://{host}/geo/v2/city/lookup",
                       headers=headers, params={"location": city, "number": 10, "lang": "zh"})
    places = geo.get("location")
    if str(geo.get("code")) != "200" or not isinstance(places, list) or not places:
        raise SupplierError("和风天气地点查询失败，请检查城市名称及 GeoAPI 权限")
    matches = [p for p in places if isinstance(p, dict) and _city_key(p.get("name", "")) == _city_key(city)]
    if len(matches) == 1:
        place = matches[0]
    elif not matches and len(places) == 1 and isinstance(places[0], dict):
        place = places[0]
    else:
        raise SupplierError("和风天气返回多个同名地点，请补充省份或地区后查询", 422)
    try:
        lat, lon = float(place["lat"]), float(place["lon"])
        if not math.isfinite(lat) or not math.isfinite(lon) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise SupplierError("和风天气地点坐标格式异常") from None
    data = request_json(client, "GET", f"https://{host}/weather/v1/daily/{lat:.2f}/{lon:.2f}",
                        headers=headers, params={"days": 7, "localTime": "true", "lang": "zh"})
    try:
        casts = []
        for item in data["days"][:7]:
            low, high = item["temperatureMin"]["value"], item["temperatureMax"]["value"]
            if not all(isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) for n in (low, high)):
                raise ValueError()
            if item["temperatureMin"]["unit"] != "°C" or item["temperatureMax"]["unit"] != "°C":
                raise ValueError()
            casts.append({"date": datetime.fromisoformat(item["daytime"]["forecastStartTime"].replace("Z", "+00:00")).date().isoformat(),
                          "day": str(item["daytime"]["condition"]["text"]),
                          "night": str(item["nighttime"]["condition"]["text"]),
                          "temp_low": str(round(low, 1)), "temp_high": str(round(high, 1))})
        if not casts:
            raise ValueError()
        attributions = data["metadata"]["attributions"]
        if not isinstance(attributions, list) or not attributions or not all(isinstance(a, str) and len(a) <= 2048 for a in attributions):
            raise ValueError()
    except (KeyError, IndexError, ValueError, TypeError, AttributeError):
        raise SupplierError("和风天气预报格式异常，请检查已开通的每日预报接口") from None
    place_name = place.get("name")
    return {"ok": True, "city": place_name if isinstance(place_name, str) and place_name.strip() else city,
            "provider": "qweather",
            "source": "和风天气", "attributions": attributions, "casts": casts}


def _load_forecast(city, client, host, key):
    try:
        with provider_slot("qweather"):
            if client is not None:
                return _fetch(city, client, host, key)
            with httpx.Client(timeout=httpx.Timeout(12, connect=5), trust_env=False, follow_redirects=False) as connection:
                return _fetch(city, connection, host, key)
    except ProviderBusy:
        raise SupplierError("天气查询繁忙，请稍后查询", 429) from None


def _valid_forecast(value):
    if not isinstance(value, dict) or value.get("ok") is not True or value.get("provider") != "qweather":
        return False
    city, attributions, casts = value.get("city"), value.get("attributions"), value.get("casts")
    day_fields = ("date", "day", "night", "temp_low", "temp_high")
    return (
        isinstance(city, str) and bool(city.strip())
        and value.get("source") == "和风天气"
        and isinstance(attributions, list) and bool(attributions)
        and all(isinstance(item, str) and len(item) <= 2048 for item in attributions)
        and isinstance(casts, list) and 1 <= len(casts) <= 7
        and all(isinstance(day, dict) and all(isinstance(day.get(field), str) for field in day_fields)
                for day in casts)
    )


def fetch_weather(city, *, client=None):
    key = settings.qweather_api_key
    if not key:
        raise SupplierError("和风天气尚未配置 API key", 503)
    host = api_host()
    cache_key = (host, hashlib.sha256(key.encode()).hexdigest(), city)
    shared = provider_cache()
    if shared is not None:
        try:
            result, _ = shared.get_or_load(
                ("qweather-daily-v1", *cache_key),
                lambda: _load_forecast(city, client, host, key),
                ttl=_FORECAST_TTL, validate=_valid_forecast,
                # A cold load makes two requests, each with its own network timeout.
                lease_seconds=35,
            )
            return result
        except CacheUnavailable:
            raise SupplierError("天气缓存暂不可用或查询仍在处理，请稍后重试", 503) from None
    with _lock:
        cached = _cache.get(cache_key)
        if cached is not None:
            return deepcopy(cached)
    result = _load_forecast(city, client, host, key)
    with _lock:
        _cache[cache_key] = deepcopy(result)
    return result
