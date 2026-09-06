"""M4 天气服务：高德天气接口（并入行程结果）。"""
import httpx

from app.core.config import settings

API_URL = "https://restapi.amap.com/v3/weather/weatherInfo"


def fetch_weather(city_adcode: str | None = None, city_name: str = "") -> dict:
    """查实时+预报天气。失败返回 {"ok": False}，不打断主流程。"""
    try:
        resp = httpx.get(
            API_URL,
            params={
                "key": settings.amap_api_key,
                "city": city_adcode or city_name,
                "extensions": "all",
            },
            timeout=10,
        )
        data = resp.json()
        forecasts = data.get("forecasts") or []
        if data.get("status") != "1" or not forecasts:
            return {"ok": False, "city": city_name}
        f = forecasts[0]
        return {
            "ok": True,
            "city": f.get("city", city_name),
            "casts": [
                {
                    "date": c.get("date", ""),
                    "day": c.get("dayweather", ""),
                    "night": c.get("nightweather", ""),
                    "temp_low": c.get("nighttemp", ""),
                    "temp_high": c.get("daytemp", ""),
                }
                for c in (f.get("casts") or [])[:4]
            ],
        }
    except (httpx.HTTPError, ValueError, KeyError):
        return {"ok": False, "city": city_name}
