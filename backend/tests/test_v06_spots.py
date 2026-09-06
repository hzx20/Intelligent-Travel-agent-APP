"""v0.6 景点只读接口测试：列表筛选分页 / 热门 / 猜你喜欢 / 详情。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.db.models import Comment, Favorite, Spot, User  # noqa: E402
from app.security import hash_password  # noqa: E402


def _seed(db):
    """造 14 个景点（成都 8 / 杭州 6）+ 2 用户 + 收藏 + 评论。"""
    spots = []
    for i in range(8):
        s = Spot(name=f"成都景点{i}", city="成都", district="青羊区",
                 tags=f"标签{i},公园" if i % 2 == 0 else f"标签{i}",
                 is_free=(i % 3 == 0), amap_id=f"C{i}", lng=104.0, lat=30.6)
        spots.append(s)
    for i in range(6):
        spots.append(Spot(name=f"杭州景点{i}", city="杭州", district="西湖区",
                          tags="湖光山色", is_free=True, amap_id=f"H{i}", lng=120.1, lat=30.2))
    db.add_all(spots)
    u1 = User(username="alice", password_hash=hash_password("pass123456"), nickname="小爱")
    u2 = User(username="bob", password_hash=hash_password("pass123456"), nickname="小波")
    db.add_all([u1, u2])
    db.commit()
    # alice 收藏：成都0、成都2（免费）×2 次；bob 收藏：杭州0
    db.add_all([
        Favorite(user_id=u1.id, spot_id=spots[0].id),
        Favorite(user_id=u1.id, spot_id=spots[2].id),
        Favorite(user_id=u2.id, spot_id=spots[8].id),
    ])
    db.add(Comment(user_id=u1.id, spot_id=spots[0].id, content="很值得逛", rating=5))
    db.commit()
    return spots, u1, u2


def test_list_filter_and_paging(client):
    c, Session = client
    db = Session()
    _seed(db)
    db.close()

    # 全量 14 条，每页 12 → 2 页
    r = c.get("/api/spots").json()
    assert r["total"] == 14 and len(r["items"]) == 12
    r2 = c.get("/api/spots", params={"page": 2}).json()
    assert len(r2["items"]) == 2
    # 城市筛选
    assert c.get("/api/spots", params={"city": "杭州"}).json()["total"] == 6
    # 关键词（标签模糊）
    assert c.get("/api/spots", params={"keyword": "湖光"}).json()["total"] == 6
    # 免费筛选：i%3==0 → 0,3,6 三条成都免费 + 杭州全免费 6 条 = 9
    assert c.get("/api/spots", params={"is_free": "free"}).json()["total"] == 9
    assert c.get("/api/spots", params={"is_free": "paid"}).json()["total"] == 5


def test_hot_by_favorites(client):
    c, Session = client
    db = Session()
    spots, _, _ = _seed(db)
    db.close()
    hot = c.get("/api/spots/hot").json()
    assert len(hot) == 4
    assert hot[0]["id"] == spots[0].id, "收藏数最高的应排第一"


def test_for_you_guest_and_logged_in(client):
    c, Session = client
    db = Session()
    spots, u1, _ = _seed(db)
    db.close()
    # 游客：返回分页结构，页 1 满 12 条，页 2 为剩余 2 条（种子共 14 条）且与页 1 不重叠
    guest = c.get("/api/spots/for-you-guest").json()
    assert len(guest["items"]) == 12 and guest["total"] == 14
    p2 = c.get("/api/spots/for-you-guest", params={"page": 2}).json()
    assert len(p2["items"]) == 2 and guest["total"] == 14
    ids1 = {s["id"] for s in guest["items"]}
    assert all(s["id"] not in ids1 for s in p2["items"]), "页 2 不应与页 1 重复"
    # 登录用户：alice 收藏了"公园"标签景点 → 偏好命中应包含公园类
    login = c.post("/api/auth/login", json={"username": "alice", "password": "pass123456"}).json()
    mine = c.get("/api/spots/for-you",
                 headers={"Authorization": f"Bearer {login['token']}"}).json()
    assert len(mine["items"]) == 12
    assert all(s["tags"] for s in mine["items"]), "偏好推荐应命中带标签景点"
    # 未登录访问 /for-you 应 401
    assert c.get("/api/spots/for-you").status_code == 401


def test_detail_with_count_and_comments(client):
    c, Session = client
    db = Session()
    spots, _, _ = _seed(db)
    sid = spots[0].id
    db.close()
    r = c.get(f"/api/spots/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert body["favorite_count"] == 1
    assert body["comments"][0]["content"] == "很值得逛"
    assert body["comments"][0]["username"] == "alice"
    assert body["source"] == "高德地图开放平台"
    assert c.get("/api/spots/99999").status_code == 404
