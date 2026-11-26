"""Resolve itinerary locations to a named POI or keep their identity pending."""
import time
import math
import re
import unicodedata

import httpx
from sqlalchemy import or_

from app.core.config import settings
from app.core.provider_budget import ProviderBusy, amap_get
from app.db.models import Spot

API_URL = "https://restapi.amap.com/v5/place/text"
REQUEST_INTERVAL = 0.4  # 个人 key QPS=3
_CANDIDATE_LIMIT = 20
_SCENIC_SUFFIX = re.compile(r'(?:风景名胜区|风景旅游区|风景区|景区)$')
_AUXILIARY_SUFFIX = re.compile(r'(?:停车场|入口|出口|(?:东|西|南|北|正)门)$')
_BRANCH_SUFFIX = re.compile(r'(?:馆区|馆|院区|院|校区|园区|分店|店)$')
_INSTITUTION_SUFFIX = re.compile(r'(?:馆|院|大学|学校|公园|酒店|餐厅|餐馆|商场|门店)$')


_PLACEHOLDERS = {
    '', '酒店', '酒店内', '酒店附近', '住宿', '餐馆', '餐厅', '附近餐馆', '附近餐厅',
    '周边餐馆', '周边餐厅', '当地餐馆', '当地餐厅', '景区内餐馆', '景区内餐厅',
    '早餐', '午餐', '晚餐', '休息', '自由活动', '返程', '出发地', '目的地',
    '机场', '车站', '火车站',
}


def _placeholder(name, city):
    name, city = ''.join(str(name or '').split()), ''.join(str(city or '').split())
    base = city.removesuffix('市')
    cities = {city, base, base + '市'} if city else set()
    return name in _PLACEHOLDERS or name in cities or any(
        name == prefix + generic for prefix in cities for generic in _PLACEHOLDERS if generic)


def _unverified(spot, reason, candidates=()):
    # A model's guessed coordinates/identity must not survive a failed match.
    return {**spot, 'lng': None, 'lat': None, 'amap_id': '', 'address': '', 'district': '', 'poi_type': '',
            'verification_status': 'unverified', 'verification_note': reason, 'replacedNote': '',
            'verification_candidates': [{key: candidate[key] for key in ('name', 'address', 'district')}
                                        for candidate in candidates[:5]]}


def _positioned(hit):
    if not isinstance(hit, dict) or not isinstance(hit.get('amap_id'), str) or not hit['amap_id'].strip():
        return False
    lng, lat = hit.get('lng'), hit.get('lat')
    return (type(lng) in (int, float) and type(lat) in (int, float)
            and math.isfinite(lng) and math.isfinite(lat) and -180 <= lng <= 180 and -90 <= lat <= 90)


def _text(value, limit):
    return value.strip()[:limit] if isinstance(value, str) else ''


def _city_key(city):
    return _text(city, 80).removesuffix('市')


def _name_key(name, city):
    name = unicodedata.normalize('NFKC', name).casefold()
    name = re.sub(r'[\s()\-–—·]+', '', name)
    prefix = _city_key(city)
    if prefix and name.startswith(prefix + '市') and len(name) > len(prefix) + 1:
        name = prefix + name[len(prefix) + 1:]
    return name


def _relation(wanted, found):
    if wanted == found:
        return 3
    if _AUXILIARY_SUFFIX.search(found):
        return 0
    if _SCENIC_SUFFIX.sub('', wanted) == _SCENIC_SUFFIX.sub('', found):
        return 2
    if (_INSTITUTION_SUFFIX.search(wanted) and found.startswith(wanted)
            and _BRANCH_SUFFIX.search(found[len(wanted):])):
        return 1
    return 0


def _amap_search(client: httpx.Client, name: str, city: str) -> list[dict]:
    if not settings.amap_api_key or not city or len(name) > 80:
        return []
    try:
        resp = amap_get(
            API_URL, client=client,
            params={
                "key": settings.amap_api_key,
                "keywords": name,
                "region": city,
                "city_limit": "true",
                "page_size": _CANDIDATE_LIMIT,
                "page_num": 1,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict) or data.get('status') != '1' or not isinstance(data.get('pois'), list):
            return []
    except (httpx.HTTPError, ValueError, ProviderBusy):
        return []
    candidates = []
    for poi in data['pois'][:_CANDIDATE_LIMIT]:
        if not isinstance(poi, dict) or _city_key(poi.get('cityname')) != _city_key(city):
            continue
        location = poi.get('location')
        if not isinstance(location, str):
            continue
        try:
            lng, lat = (float(x) for x in location.split(','))
        except ValueError:
            continue
        candidates.append({'amap_id': _text(poi.get('id'), 100), 'name': _text(poi.get('name'), 160),
                           'lng': lng, 'lat': lat, 'address': _text(poi.get('address'), 300),
                           'district': _text(poi.get('adname'), 100), 'poi_type': _text(poi.get('type'), 200),
                           'source': 'amap'})
    return candidates


def _db_lookup(db, name: str, city: str) -> list[dict]:
    base = _city_key(city)
    if not name or not base:
        return []
    plain = unicodedata.normalize('NFKC', name)
    variants = {name, plain, _name_key(name, city), plain.replace('(', '（').replace(')', '）')}
    if name.startswith(base) and not name.startswith(base + '市'):
        variants.add(base + '市' + name[len(base):])
    rows = (db.query(Spot).filter(Spot.city.in_([base, base + '市']),
                                 or_(*(Spot.name.contains(variant, autoescape=True) for variant in sorted(variants) if variant)))
            .order_by((Spot.name == name).desc(), Spot.id).limit(_CANDIDATE_LIMIT))
    return [{'amap_id': row.amap_id, 'name': row.name, 'lng': row.lng, 'lat': row.lat,
             'address': row.address or '', 'district': row.district or '', 'poi_type': row.tags or '',
             'source': 'db'} for row in rows]


def _resolve(name, city, candidates):
    wanted = _name_key(name, city)
    matches = {}
    for candidate in candidates:
        if not _positioned(candidate) or not _text(candidate.get('name'), 160):
            continue
        score = _relation(wanted, _name_key(candidate['name'], city))
        if score and score >= matches.get(candidate['amap_id'], (0, None))[0]:
            matches[candidate['amap_id']] = score, candidate
    ranked = sorted(matches.values(), key=lambda row: -row[0])
    if not ranked:
        return None, []
    best, candidate = ranked[0]
    if best >= 2 and not any(score in (1, best) for score, _ in ranked[1:]):
        return candidate, []
    return None, [candidate for _, candidate in ranked]


def _dedupe_resolved(spots: list[dict]) -> tuple[list[dict], int]:
    """核实之后按 amap_id 再去一次重。

    命名格式不同的地点可能匹配到同一个 POI。匹配完成后再用真实 ID 收敛。
    """
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    out: list[dict] = []
    for s in spots:
        sid = str(s.get("amap_id") or "")
        nm = str(s.get("name") or "").strip()
        if sid:
            if sid in seen_ids:
                continue
            seen_ids.add(sid)
        elif nm:
            if nm in seen_names:
                continue
            seen_names.add(nm)
        out.append(s)
    return out, len(spots) - len(out)


def _dedupe_day(spots: list[dict]) -> tuple[list[dict], int]:
    """同一天内按地点名去重，返回 (新列表, 去掉的条数)。

    模型偶尔会把同一个地方排两次（实测出现过"14:00 颐和园、17:00 颐和园"）。
    同一天内同名重复几乎都是生成错误，而不是"上午下午各去一次"——时间字段并不同、
    理由也没单独写。放在核实之前做，顺带少查两次高德。
    """
    seen: set[str] = set()
    out: list[dict] = []
    for s in spots:
        key = str(s.get("name") or "").strip()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(s)
    return out, len(spots) - len(out)


def verify_itinerary_spots(
    itinerary: dict, city: str, db, amap_enabled: bool = True
) -> tuple[dict, list[dict]]:
    """核实行程中的全部地点，返回 (新行程, 核实日志)。

    itinerary 结构（与 M2 生成格式一致）：{"days": [{"day": 1, "spots": [{"name":..}, ...], ...}]}
    日志条目：{"spot": 原名, "action": db|amap|unverified|dedupe, "detail": 说明}
    """
    logs: list[dict] = []
    new_itinerary = {**itinerary, "days": []}
    days = itinerary.get("days") or []
    client = httpx.Client() if amap_enabled else None
    resolved_names = {}
    try:
        for day in days:
            new_day = {**day, "spots": []}
            raw_spots, dropped = _dedupe_day(day.get("spots", []))
            if dropped:
                logs.append({
                    "spot": day.get("theme") or f"第{day.get('day')}天",
                    "action": "dedupe",
                    "detail": f"同一天重复地点已合并（{len(raw_spots) + dropped} → {len(raw_spots)}）",
                })
            for spot in raw_spots:
                name = _text(spot.get('name'), 160)
                if _placeholder(name, city) or not _name_key(name, city):
                    reason = '尚未指定具体地点，请选择后再核实位置'
                    new_day['spots'].append(_unverified(spot, reason))
                    logs.append({'spot': name, 'action': 'unverified', 'detail': reason})
                    continue
                if name not in resolved_names:
                    local = _db_lookup(db, name, city)
                    hit, choices = _resolve(name, city, local)
                    looks_unqualified = _INSTITUTION_SUFFIX.search(_name_key(name, city)) and not re.search(r'[（(]', name)
                    if client is not None and (hit is None or looks_unqualified):
                        time.sleep(REQUEST_INTERVAL)
                        hit, choices = _resolve(name, city, local + _amap_search(client, name, city))
                    resolved_names[name] = hit, choices
                hit, choices = resolved_names[name]
                if hit is None:
                    reason = ('地点尚未唯一匹配，请核对馆区、分店或地址' if choices
                              else '未能匹配具体地点，请核对后再使用位置')
                    logs.append({'spot': name, 'action': 'unverified', 'detail': reason})
                    new_day['spots'].append(_unverified(spot, reason, choices))
                    continue
                action = hit['source']
                resolved = {key: value for key, value in hit.items() if key != 'source'}
                new_spot = {**spot, **resolved, 'verification_status': 'verified',
                            'verification_note': '位置已匹配，开放安排、票价和适配情况仍需确认',
                            'verification_candidates': [], 'replacedNote': ''}
                new_day["spots"].append(new_spot)
                detail = {
                    "db": "本地高德库命中",
                    "amap": "高德接口核实通过",
                }[action]
                logs.append({"spot": name, "action": action, "detail": detail})
            # 核实完再收一次：不同说法可能落到同一个真实 POI（"颐和园"/"颐和园北宫门"）
            resolved, dup_ids = _dedupe_resolved(new_day["spots"])
            if dup_ids:
                new_day["spots"] = resolved
                logs.append({
                    "spot": day.get("theme") or f"第{day.get('day')}天",
                    "action": "dedupe",
                    "detail": f"核实后发现同一地点被排了两次，已合并（{len(resolved) + dup_ids} → {len(resolved)}）",
                })
            new_itinerary["days"].append(new_day)
    finally:
        if client is not None:
            client.close()
    return new_itinerary, logs
