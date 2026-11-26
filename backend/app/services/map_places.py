"""User-initiated AMap candidate search; never expose the service key."""
import math
import threading
import time
import httpx
from app.core.config import settings
from app.core.provider_budget import ProviderBusy, amap_get

_rate_lock = threading.Lock()
_last_request = 0.0
NEARBY_TYPES = {
    'food': ('050100|050200|050300', ('0501', '0502', '0503')),
    'museum': ('140100|140200|140400', ('1401', '1402', '1404')),
}
NEARBY_RADIUS_M = 1500


class PlaceSearchUnavailable(Exception):
    pass


def _text(value, limit):
    return value.strip()[:limit] if isinstance(value, str) else ''


def _fetch(path, params):
    global _last_request
    if not settings.amap_api_key:
        raise PlaceSearchUnavailable('地图地点搜索尚未配置')
    with _rate_lock:
        now = time.monotonic()
        if now - _last_request < 0.4:
            raise PlaceSearchUnavailable('地图搜索请求较频繁，请稍后重试')
        _last_request = now
    try:
        response = amap_get(f'https://restapi.amap.com/v5/place/{path}', params={
            'key': settings.amap_api_key, 'page_size': 20, 'page_num': 1, **params,
        }, timeout=8.0)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or data.get('status') != '1' or not isinstance(data.get('pois'), list):
            raise ValueError()
    except (httpx.HTTPError, ValueError, ProviderBusy):
        raise PlaceSearchUnavailable('地图地点搜索暂不可用，请稍后重试') from None
    return data['pois'][:20]


def _parse_pois(pois):
    results = []
    seen = set()
    for poi in pois:
        if not isinstance(poi, dict):
            continue
        amap_id = _text(poi.get('id'), 100)
        name = _text(poi.get('name'), 160)
        location = poi.get('location')
        if not amap_id or not name or amap_id in seen or not isinstance(location, str):
            continue
        try:
            lng, lat = (float(value) for value in location.split(','))
            if not (math.isfinite(lng) and math.isfinite(lat) and -180 <= lng <= 180 and -90 <= lat <= 90):
                continue
        except (TypeError, ValueError):
            continue
        seen.add(amap_id)
        results.append({'amap_id': amap_id, 'name': name, 'lng': lng, 'lat': lat,
                        'address': _text(poi.get('address'), 300),
                        'city': _text(poi.get('cityname'), 100),
                        'district': _text(poi.get('adname'), 100),
                        'source': '高德地图'})
    return results


def search_places(keyword, city):
    return _parse_pois(_fetch('text', {'keywords': keyword, 'region': city, 'city_limit': 'true'}))


def _distance_m(lng, lat, point):
    lat_delta = math.radians(point['lat'] - lat)
    lng_delta = math.radians(point['lng'] - lng)
    chord = (math.sin(lat_delta / 2) ** 2
             + math.cos(math.radians(lat)) * math.cos(math.radians(point['lat'])) * math.sin(lng_delta / 2) ** 2)
    return round(12742000 * math.asin(math.sqrt(min(1.0, max(0.0, chord)))))


def search_nearby(lng, lat, category, exclude_id=''):
    types, prefixes = NEARBY_TYPES[category]
    payload = _fetch('around', {'location': f'{lng:.6f},{lat:.6f}', 'radius': NEARBY_RADIUS_M,
                                'types': types, 'sortrule': 'distance'})
    items = _parse_pois(poi for poi in payload if isinstance(poi, dict)
                        and isinstance(poi.get('typecode'), str) and poi['typecode'].startswith(prefixes))
    for item in items:
        item['straight_line_m'] = _distance_m(lng, lat, item)
    return sorted((item for item in items if item['amap_id'] != exclude_id
                   and item['straight_line_m'] <= NEARBY_RADIUS_M), key=lambda item: item['straight_line_m'])[:10]
