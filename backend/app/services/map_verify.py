"""M3 地点核实服务：AI 生成的地点逐个查高德，查无此地自动替换。

核实规则（对齐产品口径"AI 生成的每个地点先经地图接口核实"）：
1. 先查本地 spots 库（高德 POI 已入库）——命中直接用（零网络开销）
2. 库里没有 → 调高德 v5 搜索接口（region=城市）——命中取真实坐标
3. 仍未命中 → 从库里找同城市替代景点（保留天数节奏），并记录替换原因
"""
import time
from typing import Any

import httpx

from app.core.config import settings
from app.db.models import Spot

API_URL = "https://restapi.amap.com/v5/place/text"
REQUEST_INTERVAL = 0.4  # 个人 key QPS=3


def _amap_search(client: httpx.Client, name: str, city: str) -> dict | None:
    """高德搜索：命中返回 {amap_id, name, lng, lat, address}，未命中返回 None。"""
    try:
        resp = client.get(
            API_URL,
            params={
                "key": settings.amap_api_key,
                "keywords": name,
                "region": city,
                "city_limit": "true",
                "page_size": 1,
                "show_fields": "photos",
            },
            timeout=10,
        )
        data = resp.json()
        if data.get("status") != "1":
            return None
        pois = data.get("pois") or []
        if not pois:
            return None
        poi = pois[0]
        lng = lat = None
        if "," in poi.get("location", ""):
            try:
                lng, lat = (float(x) for x in poi["location"].split(","))
            except ValueError:
                pass
        return {
            "amap_id": poi.get("id", ""),
            "name": poi.get("name", name),
            "lng": lng,
            "lat": lat,
            "address": poi.get("address", ""),
        }
    except (httpx.HTTPError, ValueError, KeyError):
        return None


def _db_lookup(db, name: str, city: str) -> dict | None:
    """本地库精确/前缀匹配（已核实的真实 POI）。"""
    spot = (
        db.query(Spot)
        .filter(Spot.city == city, Spot.name.like(f"%{name}%"))
        .first()
    )
    if spot is None:
        return None
    return {
        "amap_id": spot.amap_id,
        "name": spot.name,
        "lng": spot.lng,
        "lat": spot.lat,
        "address": spot.address,
    }


def _db_substitute(db, city: str, used_ids: set[str]) -> dict | None:
    """同城替代：挑一个未用过的库内景点。"""
    q = db.query(Spot).filter(Spot.city == city).order_by(Spot.id)
    for spot in q.limit(50):
        if spot.amap_id not in used_ids:
            return {
                "amap_id": spot.amap_id,
                "name": spot.name,
                "lng": spot.lng,
                "lat": spot.lat,
                "address": spot.address,
            }
    return None


def verify_itinerary_spots(
    itinerary: dict, city: str, db, amap_enabled: bool = True
) -> tuple[dict, list[dict]]:
    """核实行程中的全部地点，返回 (新行程, 核实日志)。

    itinerary 结构（与 M2 生成格式一致）：{"days": [{"day": 1, "spots": [{"name":..}, ...], ...}]}
    日志条目：{"spot": 原名, "action": db|amap|replaced|keep, "detail": 说明}
    """
    logs: list[dict] = []
    used_ids: set[str] = set()
    new_itinerary = {**itinerary, "days": []}
    days = itinerary.get("days") or []

    client = httpx.Client() if amap_enabled else None
    try:
        for day in days:
            new_day = {**day, "spots": []}
            for spot in day.get("spots", []):
                name = spot.get("name", "")
                hit = _db_lookup(db, name, city)
                action = "db"
                if hit is None and client is not None:
                    time.sleep(REQUEST_INTERVAL)
                    hit = _amap_search(client, name, city)
                    action = "amap"
                if hit is None:
                    sub = _db_substitute(db, city, used_ids)
                    if sub is not None:
                        hit = sub
                        action = "replaced"
                    else:
                        logs.append({"spot": name, "action": "unverified",
                                     "detail": "高德与本地库均未找到，保留原名展示"})
                        new_day["spots"].append(spot)
                        continue
                used_ids.add(hit["amap_id"])
                new_spot = {**spot, **hit}
                new_day["spots"].append(new_spot)
                detail = {
                    "db": "本地高德库命中",
                    "amap": "高德接口核实通过",
                    "replaced": f"查无此地，已替换为 {hit['name']}",
                }[action]
                logs.append({"spot": name, "action": action, "detail": detail})
            new_itinerary["days"].append(new_day)
    finally:
        if client is not None:
            client.close()
    return new_itinerary, logs
