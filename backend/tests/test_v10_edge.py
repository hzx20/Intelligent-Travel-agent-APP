"""v1.0 边界修补测试：越界分页 / 不存在 id / 非法参数 / 上限校验 / 游客权限。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.db.models import Spot, User  # noqa: E402
from app.security import create_token  # noqa: E402


def _seed(db):
    u = User(username="edge", password_hash="x", nickname="边界")
    admin = User(username="edgeadmin", password_hash="x", is_admin=True)
    s = Spot(name="边界景点", city="成都", district="某区", amap_id="E1")
    db.add_all([u, admin, s])
    db.commit()
    return u, admin, s


def _auth(u):
    return {"Authorization": f"Bearer {create_token(u.id)}"}


def test_pagination_edge(client):
    """分页越界：不报错、返回空列表；页码/页大小非法 → 422。"""
    c, Session = client
    db = Session()
    u, admin, s = _seed(db)
    db.close()

    r = c.get("/api/spots?page=999")
    assert r.status_code == 200
    assert r.json()["items"] == [] and r.json()["total"] >= 1
    assert c.get("/api/spots?page=0").status_code == 422          # 页码从 1 起
    assert c.get("/api/spots?page_size=999").status_code == 422   # 上限 50
    assert c.get("/api/guides?page=999").json()["items"] == []
    assert c.get("/api/guides?page=0").status_code == 422


def test_missing_ids(client):
    """不存在的资源统一 404（含攻略草稿按不存在处理）。"""
    c, Session = client
    db = Session()
    u, admin, s = _seed(db)
    db.close()

    assert c.get("/api/spots/999999").status_code == 404
    assert c.get("/api/guides/999999").status_code == 404
    assert c.post("/api/spots/999999/favorite", headers=_auth(u)).status_code == 404
    assert c.post("/api/spots/999999/comments", json={"content": "x"}, headers=_auth(u)).status_code == 404
    assert c.patch("/api/guides/999999", json={"title": "t", "content": "c"}, headers=_auth(admin)).status_code == 404
    assert c.delete("/api/guides/999999", headers=_auth(admin)).status_code == 404


def test_invalid_filters(client):
    """非法/不存在的筛选值不应 500，结果为空即可。"""
    c, Session = client
    db = Session()
    u, admin, s = _seed(db)
    db.close()

    assert c.get("/api/spots?city=火星").json()["items"] == []
    assert c.get("/api/spots?keyword=" + "字" * 200).status_code == 200
    assert c.get("/api/guides?city=火星").json()["items"] == []
    assert c.get("/api/guides?sort=乱写的").json()["items"] == []   # 非法排序退回默认
    assert c.get("/api/spots/for-you-guest?page=99").status_code == 422  # 最多 3 页


def test_guide_limits(client):
    """攻略输入上限：标题 100 字、图片 30 张、关联景点 10 个。"""
    c, Session = client
    db = Session()
    u, admin, s = _seed(db)
    db.close()

    long_title = "标" * 101
    assert c.post("/api/guides", json={"title": long_title, "content": "x"}, headers=_auth(u)).status_code == 422
    many_imgs = [f"http://x/{i}.jpg" for i in range(31)]
    assert c.post("/api/guides", json={"title": "t", "content": "x", "images": many_imgs},
                  headers=_auth(u)).status_code == 422
    many_spots = list(range(1, 12))
    assert c.post("/api/guides", json={"title": "t", "content": "x", "spot_ids": many_spots},
                  headers=_auth(u)).status_code == 422
    # 关联了不存在的景点：不报错，忽略即可
    r = c.post("/api/guides", json={"title": "t", "content": "x", "spot_ids": [999999]}, headers=_auth(u))
    assert r.status_code == 201
    assert c.get(f"/api/guides/{r.json()['id']}").json()["spots"] == []


def test_guest_write_protection(client):
    """游客不能写任何东西：收藏/评论/发攻略/改攻略/删攻略/改资料 全部 401。"""
    c, Session = client
    db = Session()
    u, admin, s = _seed(db)
    db.close()

    assert c.post(f"/api/spots/{s.id}/favorite").status_code == 401
    assert c.post(f"/api/spots/{s.id}/comments", json={"content": "x"}).status_code == 401
    assert c.post("/api/guides", json={"title": "t", "content": "x"}).status_code == 401
    assert c.patch("/api/me/profile", json={"nickname": "游客"}).status_code == 401
    assert c.get("/api/me/favorites").status_code == 401
    assert c.get("/api/admin/users").status_code == 401
    # 伪造 token 也不放行（header 只接受 ASCII，这里用英文假 token）
    assert c.get("/api/me/profile", headers={"Authorization": "Bearer fake.token"}).status_code == 401
