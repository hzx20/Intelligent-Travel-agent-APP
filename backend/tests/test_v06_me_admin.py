"""v0.6 收尾接口测试：个人中心 + 管理后台。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.db.models import (  # noqa: E402
    AiPlan, Comment, Favorite, Guide, Spot, User,
)
from app.security import create_token, hash_password  # noqa: E402


def _seed(db):
    admin = User(username="boss", password_hash=hash_password("pass123456"),
                 nickname="管理员", is_admin=True)
    u1 = User(username="ellen", password_hash=hash_password("pass123456"), nickname="小艾")
    s1 = Spot(name="西湖", city="杭州", district="西湖区", is_free=True, amap_id="W1",
              tags="湖光山色", description="简介占位")
    s2 = Spot(name="灵隐寺", city="杭州", district="西湖区", is_free=False, amap_id="W2")
    db.add_all([admin, u1, s1, s2])
    db.commit()
    db.add_all([
        Favorite(user_id=u1.id, spot_id=s1.id),
        Comment(user_id=u1.id, spot_id=s1.id, content="很美", rating=5),
        Guide(user_id=u1.id, title="杭州一日", content="...", city="杭州"),
    ])
    db.add(AiPlan(user_id=u1.id, session_id="sess1", status="completed",
                  intent_json='{"city":"杭州","days":2}'))
    db.commit()
    return admin, u1, s1, s2


def _auth(u):
    return {"Authorization": f"Bearer {create_token(u.id)}"}


# ── 个人中心 ──
def test_me_profile_favorites_guides(client):
    c, Session = client
    db = Session()
    _, u1, _, _ = _seed(db)
    db.close()

    prof = c.get("/api/me/profile", headers=_auth(u1)).json()
    assert prof["nickname"] == "小艾" and prof["favorite_count"] == 1 and prof["guide_count"] == 1
    # 未登录 401
    assert c.get("/api/me/profile").status_code == 401

    favs = c.get("/api/me/favorites", headers=_auth(u1)).json()
    assert len(favs) == 1 and favs[0]["spot"]["name"] == "西湖" and favs[0]["created_at"]

    guides = c.get("/api/me/guides", headers=_auth(u1)).json()
    assert len(guides) == 1 and guides[0]["title"] == "杭州一日"

    # 改昵称
    r = c.patch("/api/me/profile", json={"nickname": "改名艾"}, headers=_auth(u1)).json()
    assert r["nickname"] == "改名艾"
    assert c.patch("/api/me/profile", json={"nickname": "  "}, headers=_auth(u1)).status_code == 422


# ── 管理后台 ──
def test_admin_guard(client):
    c, Session = client
    db = Session()
    _, u1, _, _ = _seed(db)
    db.close()
    # 普通用户访问后台 → 403
    assert c.get("/api/admin/users", headers=_auth(u1)).status_code == 403
    # 未登录 → 401
    assert c.get("/api/admin/users").status_code == 401


def test_admin_users_and_spots(client):
    c, Session = client
    db = Session()
    admin, u1, s1, s2 = _seed(db)
    db.close()

    # 用户列表+搜索
    users = c.get("/api/admin/users", params={"keyword": "ellen"}, headers=_auth(admin)).json()
    assert len(users) == 1 and users[0]["favorite_count"] == 1
    # 停用 ellen → ella 登录被拒
    r = c.patch(f"/api/admin/users/{u1.id}", params={"is_active": False}, headers=_auth(admin))
    assert r.json()["is_active"] is False
    assert c.post("/api/auth/login", json={"username": "ellen", "password": "pass123456"}).status_code == 403
    # 不能停用自己
    assert c.patch(f"/api/admin/users/{admin.id}", params={"is_active": False},
                   headers=_auth(admin)).status_code == 400

    # 景点列表
    spots = c.get("/api/admin/spots", headers=_auth(admin)).json()
    assert len(spots) == 2
    # 修正免费标记（粗判纠错场景）
    r2 = c.patch(f"/api/admin/spots/{s1.id}", json={"is_free": False, "price": 55.0},
                 headers=_auth(admin)).json()
    assert r2["is_free"] is False and r2["price"] == 55.0


def test_admin_comments_favorites_guides_aiplans(client):
    c, Session = client
    db = Session()
    admin, u1, s1, _ = _seed(db)
    db.close()

    # 评论列表 + 软删
    comments = c.get("/api/admin/comments", headers=_auth(admin)).json()
    assert comments["total"] == 1 and comments["items"][0]["content"] == "很美"
    cid = comments["items"][0]["id"]
    assert c.delete(f"/api/admin/comments/{cid}", headers=_auth(admin)).status_code == 204
    assert c.get("/api/admin/comments", headers=_auth(admin)).json()["total"] == 0
    assert c.delete("/api/admin/comments/99999", headers=_auth(admin)).status_code == 404

    # 收藏记录
    favs = c.get("/api/admin/favorites", headers=_auth(admin)).json()
    assert favs["total"] == 1 and favs["items"][0]["username"] == "ellen"

    # 攻略（含作者）
    guides = c.get("/api/admin/guides", headers=_auth(admin)).json()
    assert len(guides) == 1 and guides[0]["author"] == "ellen" and guides[0]["spots"] == []

    # AI 记录（intent 提取城市）
    plans = c.get("/api/admin/ai-plans", headers=_auth(admin)).json()
    assert len(plans) == 1 and plans[0]["intent"] == "杭州" and plans[0]["status"] == "completed"
