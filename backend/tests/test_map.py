"""静态地图接口测试：用假响应替身高德（不依赖网络），覆盖正常/缓存/异常三条路径。"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.routers import map as map_router  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\nfake-bytes"


@pytest.fixture()
def fake_amap(monkeypatch):
    """把内部 httpx.get 换成可控假接口，并记录调用参数。"""
    calls = []

    def _get(url, params=None, timeout=None):
        calls.append({"url": url, "params": params})
        return types.SimpleNamespace(
            status_code=200,
            headers={"content-type": "image/png"},
            content=PNG,
        )

    monkeypatch.setattr(httpx, "get", _get)
    map_router._cache.clear()
    return calls


def test_static_map_ok_and_params(fake_amap):
    c = TestClient(app)
    r = c.get("/api/map/static?points=104.06,30.57;104.05,30.66&size=640*320&zoom=12")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == PNG
    p = fake_amap[0]["params"]
    assert p["markers"].startswith("mid,0x1a6e50,A:")  # 绿标记 + A 编号
    assert p["markers"].endswith("104.06,30.57;104.05,30.66")
    assert p["paths"].endswith("104.06,30.57;104.05,30.66")  # 两点以上自动连线
    assert p["size"] == "640*320" and p["zoom"] == 12


def test_single_point_no_path(fake_amap):
    c = TestClient(app)
    r = c.get("/api/map/static?points=104.06,30.57&path=0&label=0")
    assert r.status_code == 200
    assert "paths" not in fake_amap[0]["params"]  # 单点不画线
    assert fake_amap[0]["params"]["markers"].startswith("mid,0x1a6e50,:")


def test_cache_hit(fake_amap):
    c = TestClient(app)
    q = "/api/map/static?points=104.06,30.57&size=320*160"
    assert c.get(q).status_code == 200
    assert c.get(q).status_code == 200
    assert len(fake_amap) == 1  # 第二次命中缓存，不再请求高德


def test_bad_points(fake_amap):
    c = TestClient(app)
    assert c.get("/api/map/static?points=").status_code == 400
    assert c.get("/api/map/static?points=abc,def").status_code == 400  # 全脏
    assert c.get("/api/map/static?points=104.06").status_code == 400  # 缺纬度
    assert fake_amap == []  # 参数不合法时不该打扰高德
    # 部分脏数据容错：脏段丢弃、有效段保留
    assert c.get("/api/map/static?points=abc,def;104.06,30.57").status_code == 200
    assert fake_amap[0]["params"]["markers"].endswith("104.06,30.57")


def test_upstream_error(monkeypatch, fake_amap):
    """高德返回 JSON 报错（非图片）→ 转成 502。"""
    def _get(url, params=None, timeout=None):
        return types.SimpleNamespace(
            status_code=200, headers={"content-type": "application/json"},
            content=b'{"status":"0","info":"INVALID_USER_KEY"}',
        )
    monkeypatch.setattr(httpx, "get", _get)
    c = TestClient(app)
    assert c.get("/api/map/static?points=104.06,30.57").status_code == 502


def test_point_limit(fake_amap):
    """超过 20 个点只取前 20，避免 URL 超长被拒。"""
    pts = ";".join(f"104.{i:02d},30.{i:02d}" for i in range(30))
    c = TestClient(app)
    assert c.get(f"/api/map/static?points={pts}").status_code == 200
    assert fake_amap[0]["params"]["markers"].count(";") == 19  # 20 点 → 19 个分隔符
