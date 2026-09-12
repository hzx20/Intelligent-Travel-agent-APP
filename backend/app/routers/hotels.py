"""真实酒店数据（v1.2）：高德官方"周边搜索"接口，只展示真实可溯源的信息。

数据边界（对用户的诚实承诺）：
- 高德能给：真实酒店名称、地址、实拍照片、距景点直线距离、评分（如有）、人均消费（如有）
- 高德不能给：实时房价、房型清单、评价文字——这些引导用户点按钮去 OTA 平台自己看
- 平台跳转：携程支持关键词直达列表页（已实测 200）；去哪儿/同程已下线直达格式，
  只能到官网频道页（按钮文案如实标注，不假装"直达"）
"""
from typing import Any

import httpx
from cachetools import TTLCache
from fastapi import APIRouter, Query

from app.core.config import settings

router = APIRouter(prefix="/api/hotels", tags=["hotels"])

AROUND_URL = "https://restapi.amap.com/v5/place/around"
TYPE_LODGING = "100000"  # 高德 POI 分类：住宿服务
CACHE: TTLCache = TTLCache(maxsize=256, ttl=1800)  # 同一坐标 30 分钟内复用

# 平台跳转链接（2026-09-12 实测：携程关键词直达可用；去哪儿/同程直达已下线）
def _platform_links(name: str, city: str) -> dict[str, str]:
    from urllib.parse import quote

    kw = quote(f"{city} {name}".strip())
    return {
        "ctrip": f"https://hotels.ctrip.com/hotels/list?keyword={kw}",  # 关键词直达
        "qunar": "https://hotel.qunar.com/",   # 直达已下线，落到酒店频道
        "tongcheng": "https://www.ly.com/hotel",  # 同上
    }


def _pick_photo(poi: dict) -> str:
    photos = poi.get("photos") or []
    for p in photos:
        url = (p or {}).get("url") or ""
        if url.startswith("http"):
            return url
    return ""


def _to_item(poi: dict) -> dict:
    """高德 POI → 精简酒店条目（纯函数，便于测试）。"""
    loc = poi.get("location") or ""
    lng, lat = (None, None)
    if "," in loc:
        try:
            lng, lat = (float(x) for x in loc.split(","))
        except ValueError:
            pass
    biz = poi.get("business") or {}
    rating = biz.get("rating") or ""
    cost = biz.get("cost") or ""
    return {
        "name": poi.get("name") or "",
        "address": poi.get("address") or "",
        "lng": lng,
        "lat": lat,
        "distance_m": poi.get("distance") or "",  # 周边搜索原生返回，距锚点米数
        "rating": str(rating) if rating not in ("", None) else "",
        "cost": str(cost) if cost not in ("", None) else "",
        "photo": _pick_photo(poi),
        "source": "高德地图开放平台",
    }


def _fetch_amap(lng: float, lat: float, radius: int) -> list[dict[str, Any]]:
    """调高德周边搜索，返回原始 POI 列表；接口异常返回空列表（不炸前端）。"""
    try:
        resp = httpx.get(
            AROUND_URL,
            params={
                "key": settings.amap_api_key,
                "location": f"{lng},{lat}",
                "types": TYPE_LODGING,
                "radius": radius,
                "page_size": 10,
                "show_fields": "photos,business",
            },
            timeout=10,
        )
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return []
    if data.get("status") != "1":
        return []
    return [p for p in (data.get("pois") or []) if isinstance(p, dict)]


@router.get("")
def nearby_hotels(
    lng: float = Query(..., description="锚点经度（行程第一天的核心景点）"),
    lat: float = Query(..., description="锚点纬度"),
    city: str = Query(default="", max_length=20),
    radius: int = Query(default=3000, le=10000),
):
    """住处推荐：以行程锚点为圆心搜周边真实酒店（高德官方数据，带缓存）。"""
    key = f"hotels:{lng:.4f}:{lat:.4f}:{radius}"
    if key in CACHE:
        return CACHE[key]

    items = [_to_item(p) for p in _fetch_amap(lng, lat, radius) if p.get("name")]
    for it in items:
        it["links"] = _platform_links(it["name"], city)

    payload = {
        "ok": bool(items),
        "items": items,
        "note": "" if items else "高德暂未返回该区域的酒店数据",
        "source": "高德地图开放平台（名称/地址/照片/距离真实可溯）",
    }
    CACHE[key] = payload
    return payload
