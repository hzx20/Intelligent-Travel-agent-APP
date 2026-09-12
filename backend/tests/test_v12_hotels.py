"""真实酒店接口测试：假高德响应替身（不依赖网络），覆盖正常/缓存/异常/脏数据。"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.routers import hotels as hotels_router  # noqa: E402

FAKE_POIS = {
    "status": "1",
    "pois": [
        {"name": "西湖边的酒店", "address": "龙井路1号", "location": "120.140,30.250",
         "distance": "850", "photos": [{"url": "https://aos-comment.amap.com/photo1.jpg"}],
         "business": {"rating": "4.5", "cost": "356"}},
        {"name": "没照片没评分的客栈", "address": " somewhere", "location": "120.141,30.251",
         "distance": "1200", "photos": [], "business": {}},
        "乱入的字符串",  # 脏数据：必须被跳过
        {"name": ""},  # 无名字：跳过
    ],
}


@pytest.fixture()
def fake_amap(monkeypatch):
    calls = []

    def _get(url, params=None, timeout=None):
        calls.append({"url": url, "params": params})
        return types.SimpleNamespace(status_code=200, json=lambda: FAKE_POIS)

    monkeypatch.setattr(httpx, "get", _get)
    hotels_router.CACHE.clear()
    return calls


def test_hotels_ok_shape_and_links(fake_amap):
    c = TestClient(app)
    r = c.get("/api/hotels?lng=120.14&lat=30.25&city=杭州")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and len(body["items"]) == 2, "脏数据与无名条目应被跳过"
    first = body["items"][0]
    assert first["name"] == "西湖边的酒店"
    assert first["photo"].startswith("https://")
    assert first["rating"] == "4.5" and first["cost"] == "356"
    assert first["distance_m"] == "850"
    assert first["source"] == "高德地图开放平台"
    # 平台跳转：携程关键词直达；去哪儿/同程为官网频道（直达格式已被平台下线）
    assert "hotels.ctrip.com/hotels/list" in first["links"]["ctrip"]
    assert first["links"]["qunar"] == "https://hotel.qunar.com/"
    assert first["links"]["tongcheng"] == "https://www.ly.com/hotel"
    # 请求参数：类型=住宿服务，且坐标/半径正确传给高德
    p = fake_amap[0]["params"]
    assert p["types"] == "100000" and p["location"] == "120.14,30.25" and p["radius"] == 3000


def test_hotels_cached(fake_amap):
    c = TestClient(app)
    c.get("/api/hotels?lng=120.14&lat=30.25")
    n = len(fake_amap)
    c.get("/api/hotels?lng=120.14&lat=30.25")  # 同坐标第二次 → 不再打高德
    assert len(fake_amap) == n, "缓存应命中"
    c.get("/api/hotels?lng=120.15&lat=30.26")  # 换坐标 → 重新请求
    assert len(fake_amap) == n + 1


def test_hotels_amap_down_graceful(monkeypatch):
    def _boom(url, params=None, timeout=None):
        raise httpx.ConnectError("网络断了")

    monkeypatch.setattr(httpx, "get", _boom)
    hotels_router.CACHE.clear()
    c = TestClient(app)
    r = c.get("/api/hotels?lng=120.14&lat=30.25")
    assert r.status_code == 200, "高德挂了也不能 500"
    body = r.json()
    assert body["ok"] is False and body["items"] == [] and body["note"]
