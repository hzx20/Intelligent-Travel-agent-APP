"""M4 天气服务：高德天气接口（并入行程结果）。

什么是 adcode？
    高德给每个行政区编的 6 位数字身份证（北京=110000、成都=510100）。
    天气接口用城市中文名也能查，但遇到同名、别称、带"市"字就容易查歪；
    先换成 adcode 再查，等于把模糊的中文名换成精确编号，准确率更高。

解析顺序（省钱 + 稳）：
    1. 本地字典直接命中（首批 5 城 + 常见旅游城市，零网络开销）
    2. 字典没有 → 调高德"行政区域查询"接口换 adcode，结果缓存进内存
    3. 还是失败 → 退回用中文名硬查（老办法兜底，不会比现在更差）
"""
import httpx

from app.core.config import settings

API_URL = "https://restapi.amap.com/v3/weather/weatherInfo"
DISTRICT_URL = "https://restapi.amap.com/v3/config/district"

# 首批 5 城 + 常见旅游城市（key 用去掉"市/地区"后的简称，与库里 city 字段口径一致）
CITY_ADCODE = {
    "北京": "110000",
    "成都": "510100",
    "杭州": "330100",
    "西安": "610100",
    "三亚": "460200",
    # 常见扩展：用户问到这些城市时同样零开销命中
    "上海": "310000",
    "广州": "440100",
    "深圳": "440300",
    "重庆": "500000",
    "南京": "320100",
    "苏州": "320500",
    "厦门": "350200",
    "青岛": "370200",
    "大理": "532900",
    "丽江": "530700",
    "桂林": "450300",
    "张家界": "430800",
    "拉萨": "540100",
    "哈尔滨": "230100",
    "敦煌": "620982",
}

# 内存缓存：解析过的城市不再重复调接口（进程重启即失效，无副作用）
_adcode_cache: dict[str, str] = {}


def normalize_city(city_name: str) -> str:
    """把"成都市 / 北京·朝阳区 / 三亚，天涯区"等写法收敛成简称。"""
    if not city_name:
        return ""
    name = city_name.strip()
    for sep in ("·", "，", ",", "、"):
        name = name.split(sep)[0].strip()
    for suffix in ("特别行政区", "自治州", "地区", "市", "县"):
        if name.endswith(suffix) and len(name) > len(suffix):
            name = name[: -len(suffix)]
            break
    return name


def resolve_adcode(city_name: str) -> str:
    """城市名 → adcode；拿不到返回空字符串（调用方会退回中文名查询）。"""
    key = normalize_city(city_name)
    if not key:
        return ""
    if key in CITY_ADCODE:
        return CITY_ADCODE[key]
    if key in _adcode_cache:
        return _adcode_cache[key]

    try:
        resp = httpx.get(
            DISTRICT_URL,
            params={"key": settings.amap_api_key, "keywords": key, "subdistrict": "0"},
            timeout=8,
        )
        districts = resp.json().get("districts") or []
        code = districts[0].get("adcode", "") if districts else ""
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        code = ""

    if code:
        _adcode_cache[key] = code
    return code


def fetch_weather(city_adcode: str | None = None, city_name: str = "") -> dict:
    """查实时+预报天气。失败返回 {"ok": False}，不打断主流程。

    没传 adcode 时自动解析：先字典 → 再高德行政区域查询 → 最后退回中文名。
    """
    adcode = city_adcode or resolve_adcode(city_name)
    try:
        resp = httpx.get(
            API_URL,
            params={
                "key": settings.amap_api_key,
                "city": adcode or city_name,
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
            "adcode": adcode,
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
