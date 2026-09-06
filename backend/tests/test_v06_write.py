"""v0.6 第二批写接口测试：收藏切换 / 评论发表 / 详情 favorited 字段。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.db.models import Favorite, Spot, User  # noqa: E402
from app.security import create_token  # noqa: E402


def _seed(db):
    u1 = User(username="carol", password_hash="x", nickname="小卡")
    u2 = User(username="dave", password_hash="x", nickname="小戴")
    s1 = Spot(name="西湖", city="杭州", district="西湖区", is_free=True, amap_id="W1")
    db.add_all([u1, u2, s1])
    db.commit()
    return u1, u2, s1


def _auth(u):
    return {"Authorization": f"Bearer {create_token(u.id)}"}


def test_favorite_toggle_and_count(client):
    c, Session = client
    db = Session()
    u1, u2, s1 = _seed(db)
    db.close()

    # 未登录 → 401
    assert c.post(f"/api/spots/{s1.id}/favorite").status_code == 401
    # carol 收藏 → favorited=True, count=1
    r1 = c.post(f"/api/spots/{s1.id}/favorite", headers=_auth(u1)).json()
    assert r1 == {"favorited": True, "favorite_count": 1}
    # dave 也收藏 → count=2
    r2 = c.post(f"/api/spots/{s1.id}/favorite", headers=_auth(u2)).json()
    assert r2["favorite_count"] == 2
    # carol 再点 → 取消收藏，count 回落 1
    r3 = c.post(f"/api/spots/{s1.id}/favorite", headers=_auth(u1)).json()
    assert r3 == {"favorited": False, "favorite_count": 1}
    # 不存在的景点 → 404
    assert c.post("/api/spots/99999/favorite", headers=_auth(u1)).status_code == 404


def test_comment_create_and_validation(client):
    c, Session = client
    db = Session()
    u1, _, s1 = _seed(db)
    db.close()

    # 未登录 → 401
    assert c.post(f"/api/spots/{s1.id}/comments", json={"content": "好评"}).status_code == 401
    # 正常发表（带评分）
    r = c.post(f"/api/spots/{s1.id}/comments", json={"content": "值得一来！", "rating": 5},
               headers=_auth(u1))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["content"] == "值得一来！" and body["rating"] == 5
    assert body["username"] == "carol"
    # 空内容 422 / 评分越界 422
    assert c.post(f"/api/spots/{s1.id}/comments", json={"content": "  "},
                  headers=_auth(u1)).status_code == 422
    assert c.post(f"/api/spots/{s1.id}/comments", json={"content": "好", "rating": 6},
                  headers=_auth(u1)).status_code == 422
    # 详情页评论列表含新评论
    detail = c.get(f"/api/spots/{s1.id}").json()
    assert detail["comments"][0]["content"] == "值得一来！"


def test_detail_favorited_flag(client):
    c, Session = client
    db = Session()
    u1, _, s1 = _seed(db)
    db.close()

    # 游客：favorited=False
    assert c.get(f"/api/spots/{s1.id}").json()["favorited"] is False
    # 收藏后带 token：favorited=True
    c.post(f"/api/spots/{s1.id}/favorite", headers=_auth(u1))
    r = c.get(f"/api/spots/{s1.id}", headers=_auth(u1)).json()
    assert r["favorited"] is True and r["favorite_count"] == 1
