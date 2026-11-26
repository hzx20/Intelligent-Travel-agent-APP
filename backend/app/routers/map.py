"""静态地图（v0.7 收尾）：后端代理高德静态地图，返回一个图片。

为什么走后端代理而不是前端直接拼 URL？
    静态地图的请求里必须带上高德的 key。前端拼 URL 等于把 key 写进网页源码，
    谁打开 F12 都能抄走、刷我们的调用配额。由后端代拿再转发图片，key 只留在服务器。

为什么是"静态"地图？
    可拖可缩放的 JS 地图要"Web端(JS API)"类型的 key，和现在这个"Web 服务"key
    不是同一张，得另外申请开通。静态地图属于 Web 服务，现有 key 直接用，
    代价是不能拖动缩放——换来看一眼就懂的路线全貌，够用。
"""
import httpx
import threading
import time
from typing import Literal
from cachetools import TTLCache
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings
from app.core.provider_budget import amap_get
from app.services import map_places, trip_routes

router = APIRouter(prefix="/api/map", tags=["map"])

STATIC_URL = "https://restapi.amap.com/v3/staticmap"
LABELS = "ABCDEFGHIJKLMNOPQRST"  # 静态地图标记只支持单字符编号
MAX_POINTS = 20


class PlaceSearchIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    keyword: str = Field(min_length=1, max_length=80)
    city: str = Field(min_length=1, max_length=50)

    @field_validator('keyword', 'city')
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('请输入关键词和城市')
        return value


class GuestRoutePoint(BaseModel):
    lng: float = Field(ge=-180, le=180, allow_inf_nan=False)
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    amap_id: str = Field(default='', max_length=100)


class NearbyPlaceSearchIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    category: Literal['food', 'museum']
    origin: GuestRoutePoint


class GuestRouteIn(BaseModel):
    origin: GuestRoutePoint
    destination: GuestRoutePoint
    mode: Literal['walking', 'driving'] = 'walking'


# Bounded per-peer, per-process rate limiting; deployments also need proxy limits.
_guest_route_peers = TTLCache(maxsize=2048, ttl=60)
_guest_route_lock = threading.Lock()


@router.post('/guest-route')
def guest_route(body: GuestRouteIn, request: Request, response: Response):
    peer = request.client.host if request.client else 'unknown'
    now = time.monotonic()
    with _guest_route_lock:
        recent = [stamp for stamp in _guest_route_peers.get(peer, ()) if now - stamp < 60]
        if len(recent) >= 30:
            raise HTTPException(429, '路线查询较频繁，请稍后重试')
        recent.append(now)
        _guest_route_peers[peer] = recent
    response.headers['Cache-Control'] = 'private, no-store'
    try:
        route = trip_routes.get_route(body.origin, body.destination, body.mode)
    except trip_routes.RouteUnavailable as exc:
        return {'status':'unavailable', 'message':str(exc)}
    return {'status':'ok', 'source':'高德地图', **route}


@router.post('/place-search')
def place_search(body: PlaceSearchIn | NearbyPlaceSearchIn, response: Response):
    try:
        if isinstance(body, NearbyPlaceSearchIn):
            items = map_places.search_nearby(body.origin.lng, body.origin.lat, body.category, body.origin.amap_id)
        else:
            items = map_places.search_places(body.keyword, body.city)
    except map_places.PlaceSearchUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    response.headers['Cache-Control'] = 'no-store'
    return {'items': items}


@router.get("/config")
def map_config():
    """给前端下发 JS 地图钥匙（v1.1 动态地图）。

    说明：JS key 与安全密钥本来就会出现在浏览器加载的地图脚本请求里，
    属于"公开钥匙"，下发不算泄密；真正的服务端 key（POI/天气/静态图）依然只留在后端。
    未配置时返回空串，前端据此显示"地图未配置"的引导，而不是白屏。
    """
    return {
        "js_key": settings.amap_js_key,
        "security_code": settings.amap_js_security_code,
        "enabled": bool(settings.amap_js_key),
    }

# 简单内存缓存：同样参数只下载一次（图片小、变化少，进程重启即失效）
_cache: dict[str, bytes] = {}
CACHE_MAX = 80


def _parse_points(raw: str) -> list[tuple[float, float]]:
    """解析 "lng,lat;lng,lat" → [(lng, lat)]，脏数据自动丢弃。"""
    pts: list[tuple[float, float]] = []
    for seg in (raw or "").split(";"):
        parts = seg.strip().split(",")
        if len(parts) != 2:
            continue
        try:
            pts.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    return pts[:MAX_POINTS]


@router.get("/static")
def static_map(
    points: str = Query(..., description="坐标串：lng,lat;lng,lat（最多 20 点）"),
    size: str = Query("640*320", description="图片尺寸，最大 1024*1024"),
    zoom: int = Query(11, ge=1, le=17),
    label: int = Query(1, ge=0, le=1, description="是否在标记上显示 A/B/C 编号"),
    path: int = Query(1, ge=0, le=1, description="是否按点位顺序画连线"),
):
    """生成行程路线图（PNG）。失败返回 502，前端会自动隐藏图片。"""
    pts = _parse_points(points)
    if not pts:
        raise HTTPException(400, "缺少有效坐标")

    coord = ";".join(f"{lng},{lat}" for lng, lat in pts)
    style = f"mid,0x1a6e50,{LABELS[0] if label else ''}"  # 绿标记，可选编号
    params = {
        "key": settings.amap_api_key,
        "size": size,
        "zoom": zoom,
        "markers": f"{style}:{coord}",
    }
    if path and len(pts) > 1:
        # 连线：粗细 4，绿色，半透明
        params["paths"] = f"4,0x1a6e50,0.7,,:{coord}"

    cache_key = f"{size}|{zoom}|{label}|{path}|{coord}"
    if cache_key in _cache:
        return Response(_cache[cache_key], media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})

    try:
        resp = amap_get(STATIC_URL, params=params, timeout=12)
    except httpx.HTTPError:
        raise HTTPException(502, "地图服务暂不可用")

    # 高德出错时返回 JSON（不是图片），这里识别出来转成明确的失败
    ctype = resp.headers.get("content-type", "")
    if resp.status_code != 200 or "image" not in ctype:
        raise HTTPException(502, "地图服务返回异常")

    if len(_cache) >= CACHE_MAX:
        _cache.clear()
    _cache[cache_key] = resp.content
    return Response(resp.content, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})
