"""On-demand AMap route estimates with a bounded, short-lived process cache."""
import math
import threading
import time
from datetime import datetime, timezone

import httpx
from cachetools import TTLCache

from app.core.config import settings
from app.core.provider_budget import ProviderBusy, amap_get

_cache = TTLCache(maxsize=512, ttl=900)
_cache_lock = threading.Lock()
_rate_lock = threading.Lock()
_last_request = 0.0


class RouteUnavailable(Exception):
    """Safe, user-facing reason without provider credentials or response details."""


def _coordinate(item):
    if (type(item.lng) not in (int, float) or type(item.lat) not in (int, float)
            or not -180 <= item.lng <= 180 or not -90 <= item.lat <= 90):
        return None
    return f"{item.lng:.6f},{item.lat:.6f}"


def _parse_path(payload, *, walking=False):
    if not isinstance(payload, dict) or payload.get("status") != "1":
        raise RouteUnavailable("地图服务暂不可用或未开通路线查询")
    route = payload.get("route") or {}
    paths = route.get("paths") if isinstance(route, dict) else None
    if not isinstance(paths, list) or not paths or not isinstance(paths[0], dict):
        raise RouteUnavailable("没有找到可用路线")
    path = paths[0]
    try:
        distance = int(path["distance"])
        duration = int(path["cost"]["duration"])
        if distance < 0 or duration < 0:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise RouteUnavailable("路线数据不完整，请稍后重试") from None
    points = []
    steps = path.get("steps") if isinstance(path.get("steps"), list) else []
    for step in steps:
        if not isinstance(step, dict):
            continue
        raw = step.get("polyline")
        if not isinstance(raw, str):
            continue
        for token in raw.split(";"):
            try:
                parts = token.split(",")
                if len(parts) != 2:
                    continue
                lng, lat = float(parts[0]), float(parts[1])
                if not (math.isfinite(lng) and math.isfinite(lat) and -180 <= lng <= 180 and -90 <= lat <= 90):
                    continue
                point = [lng, lat]
                if not points or points[-1] != point:
                    points.append(point)
            except ValueError:
                continue
            if len(points) >= 10000:
                break
        if len(points) >= 10000:
            break
    result = {"distance_m": distance, "duration_s": duration, "polyline": points}
    if walking:
        walk_types = {str(step['navi'].get('walk_type')) for step in steps
                      if isinstance(step, dict) and isinstance(step.get('navi'), dict)}
        result['walking_features'] = [label for code, label in [('20', 'stairs'), ('21', 'slope')] if code in walk_types]
    return result


def _get_route(origin, destination, mode, *, use_cache=True):
    """Return estimates for two stored points; never expose the web-service key."""
    global _last_request
    start, end = _coordinate(origin), _coordinate(destination)
    if start is None or end is None:
        raise RouteUnavailable("地点缺少坐标，无法查询交通")
    if not settings.amap_api_key:
        raise RouteUnavailable("尚未配置地图路线服务")
    # Include POI IDs in the key because the provider can use them to choose entrances.
    key = (mode, start, end, origin.amap_id or "", destination.amap_id or "")
    with _cache_lock:
        cached = _cache.get(key) if use_cache else None
    if cached is not None:
        return {**cached, "cached": True}
    params = {"key": settings.amap_api_key, "origin": start, "destination": end,
              "show_fields": "cost,polyline,navi" if mode == 'walking' else "cost,polyline"}
    if origin.amap_id:
        params["origin_id"] = origin.amap_id
    if destination.amap_id:
        params["destination_id"] = destination.amap_id
    try:
        # This lock coordinates callers in this process. A shared limiter is required for multiple workers.
        with _rate_lock:
            delay = 0.4 - (time.monotonic() - _last_request)
            if delay > 0:
                time.sleep(delay)
            _last_request = time.monotonic()
        response = amap_get(f"https://restapi.amap.com/v5/direction/{mode}", params=params, timeout=8.0)
        response.raise_for_status()
        result = _parse_path(response.json(), walking=mode == 'walking')
    except ProviderBusy:
        raise RouteUnavailable("地图服务繁忙，请稍后重试") from None
    except (httpx.HTTPError, ValueError):
        raise RouteUnavailable("路线查询失败，请稍后重试") from None
    result["queried_at"] = datetime.now(timezone.utc).isoformat()
    with _cache_lock:
        if use_cache: _cache[key] = result
    return {**result, "cached": False}


def _valid_cached_route(value):
    return (isinstance(value,dict) and isinstance(value.get('distance_m'),int) and value['distance_m']>=0
            and isinstance(value.get('duration_s'),int) and value['duration_s']>=0
            and isinstance(value.get('queried_at'),str) and isinstance(value.get('polyline'),list)
            and ('walking_features' not in value or isinstance(value['walking_features'], list)
                 and len(value['walking_features']) <= 2 and all(flag in ('stairs','slope') for flag in value['walking_features']))
            and len(value['polyline'])<=10000 and all(isinstance(p,list) and len(p)==2
            and all(isinstance(n,(int,float)) and math.isfinite(n) for n in p)
            and -180<=p[0]<=180 and -90<=p[1]<=90 for p in value['polyline']))


def get_route(origin,destination,mode):
    from app.core.shared_cache import provider_cache,CacheUnavailable
    cache=provider_cache()
    if cache is None:return _get_route(origin,destination,mode)
    key=['amap-route-v2',settings.amap_api_key,mode,_coordinate(origin),_coordinate(destination),origin.amap_id or '',destination.amap_id or '']
    try:
        value,hit=cache.get_or_load(key,lambda:_get_route(origin,destination,mode,use_cache=False),ttl=900,validate=_valid_cached_route)
        return {**value,'cached':hit}
    except CacheUnavailable:
        raise RouteUnavailable('路线缓存暂不可用或查询仍在处理，请稍后重试') from None
