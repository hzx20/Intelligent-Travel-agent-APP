"""v0.5 数据底座测试：模型建库 / 密码与 token / 认证接口 / POI 解析。

全部走内存 SQLite，不调外部接口（不花智谱/高德额度）。
"""
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.db.database import Base, get_db  # noqa: E402
from app.db.models import (  # noqa: E402
    AiPlan,
    Comment,
    Favorite,
    Guide,
    GuideImage,
    Spot,
    User,
)
from app.main import app  # noqa: E402
from app.security import create_token, decode_token, hash_password, verify_password  # noqa: E402
from scripts.fetch_pois import is_free_guess, parse_poi  # noqa: E402

# ── 内存库 + 覆盖 get_db：认证接口测试不落盘 ──
_mem_engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
Base.metadata.create_all(_mem_engine)
_TestSession = sessionmaker(bind=_mem_engine, autoflush=False, expire_on_commit=False)


def _override_get_db():
    db = _TestSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db
client = TestClient(app)


# ── 1. 模型建库与关系 ──
def test_models_create_and_relationships():
    db = _TestSession()
    user = User(username="model_user", password_hash=hash_password("secret1"), nickname="模型用户")
    spot = Spot(name="宽窄巷子", city="成都", district="青羊区", is_free=True,
                lng=104.05, lat=30.66, amap_id=f"TEST{uuid.uuid4().hex[:10]}")
    db.add_all([user, spot])
    db.commit()

    fav = Favorite(user_id=user.id, spot_id=spot.id)
    comment = Comment(user_id=user.id, spot_id=spot.id, content="值得逛", rating=5)
    guide = Guide(user_id=user.id, title="成都两日", content="...", city="成都")
    db.add_all([fav, comment, guide])
    db.commit()
    db.add(GuideImage(guide_id=guide.id, image_url="http://x/1.jpg", sort_order=0))
    guide.spots.append(spot)
    db.add(AiPlan(user_id=user.id, session_id="s1", status="completed",
                  intent_json='{"city":"成都"}', result_json='{"days":2}'))
    db.commit()

    assert user.favorites[0].spot.name == "宽窄巷子"
    assert spot.comments[0].rating == 5
    assert guide.images[0].image_url == "http://x/1.jpg"
    assert guide.spots[0].id == spot.id
    assert user.ai_plans[0].status == "completed"
    db.close()


# ── 2. 密码哈希 ──
def test_password_hash_roundtrip():
    stored = hash_password("my_password_1")
    assert stored.startswith("pbkdf2$"), "哈希应带方案前缀"
    assert verify_password("my_password_1", stored) is True
    assert verify_password("wrong_password", stored) is False
    assert verify_password("my_password_1", "garbage") is False


# ── 3. token 编解码 ──
def test_token_roundtrip():
    token = create_token(42)
    assert decode_token(token) == 42
    assert decode_token(token + "x") is None  # 篡改拒绝
    assert decode_token("not.a.token") is None


# ── 4. POI 解析（纯函数）──
def test_parse_poi_full_and_edge():
    full = parse_poi({
        "id": "B000A812", "name": "青城山", "type": "风景名胜;风景名胜;国家级景区",
        "address": "都江堰市青城山路", "cityname": "成都市", "adname": "都江堰市",
        "location": "103.56,30.90", "photos": [{"title": "门", "url": "http://img/1.jpg"}],
    })
    assert full["name"] == "青城山"
    assert full["city"] == "成都", "城市口径应归一化：成都市→成都"
    assert full["lng"] == 103.56 and full["lat"] == 30.90
    assert full["image_url"] == "http://img/1.jpg"
    assert full["is_free"] is False, "山岳景区不应粗判为免费"
    assert "国家级景区" in full["tags"]

    broken = parse_poi({"id": "B001", "name": "无名湖", "type": "公园;广场",
                        "location": "bad-loc", "photos": []})
    assert broken["lng"] is None and broken["lat"] is None, "坏坐标应安全降级为 None"
    assert broken["image_url"] == "", "无照片应留空"
    assert broken["is_free"] is True, "湖/公园类应粗判为免费"


def test_is_free_guess():
    assert is_free_guess("人民公园", "公园") is True
    assert is_free_guess("锦里古街", "街区") is True
    assert is_free_guess("兵马俑博物馆", "博物馆") is False


# ── 5. 注册/登录/我 接口流 ──
def test_register_login_me_flow():
    uname = f"v05_{uuid.uuid4().hex[:8]}"
    # 注册 201
    r = client.post("/api/auth/register", json={"username": uname, "password": "pass123456"})
    assert r.status_code == 201, r.text
    assert r.json()["nickname"] == uname
    # 重复注册 409
    r2 = client.post("/api/auth/register", json={"username": uname, "password": "pass123456"})
    assert r2.status_code == 409
    # 用户名过短 422
    r3 = client.post("/api/auth/register", json={"username": "ab", "password": "pass123456"})
    assert r3.status_code == 422
    # 登录 200 + token
    r4 = client.post("/api/auth/login", json={"username": uname, "password": "pass123456"})
    assert r4.status_code == 200, r4.text
    token = r4.json()["token"]
    # 错密码 401
    r5 = client.post("/api/auth/login", json={"username": uname, "password": "wrong123456"})
    assert r5.status_code == 401
    # me：带 token 200 / 不带 401 / 带坏 token 401
    r6 = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r6.status_code == 200 and r6.json()["username"] == uname
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer broken"}).status_code == 401
