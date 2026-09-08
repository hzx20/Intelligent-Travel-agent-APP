"""v0.8 攻略接口测试：发布 / 草稿可见性 / 检索 / 权限 / 浏览量。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.db.models import Spot, User  # noqa: E402
from app.security import create_token  # noqa: E402


def _seed(db):
    author = User(username="achai", password_hash="x", nickname="阿茶")
    other = User(username="bob", password_hash="x")
    admin = User(username="root", password_hash="x", is_admin=True)
    s1 = Spot(name="宽窄巷子", city="成都", district="青羊区", amap_id="A1")
    s2 = Spot(name="大熊猫繁育研究基地", city="成都", district="成华区", amap_id="A2")
    db.add_all([author, other, admin, s1, s2])
    db.commit()
    return author, other, admin, s1, s2


def _auth(u):
    return {"Authorization": f"Bearer {create_token(u.id)}"}


def _new_guide(**kw):
    base = {
        "title": "成都 3 天 2 晚亲子悠闲攻略",
        "content": "Day1 宽窄巷子 → 人民公园 → 武侯祠 → 锦里，全程顺路不折腾。",
        "city": "成都",
        "images": ["http://img/1.jpg", "http://img/2.jpg"],
        "spot_ids": [],
    }
    base.update(kw)
    return base


def test_create_list_detail(client):
    c, Session = client
    db = Session()
    author, other, admin, s1, s2 = _seed(db)
    db.close()

    # 未登录发攻略 → 401
    assert c.post("/api/guides", json=_new_guide()).status_code == 401
    # 作者发布 → 201
    r = c.post("/api/guides", json=_new_guide(spot_ids=[s1.id, s2.id]), headers=_auth(author))
    assert r.status_code == 201, r.text
    gid = r.json()["id"]

    # 列表可见，摘要与关联景点数正确
    lst = c.get("/api/guides").json()
    assert lst["total"] == 1
    item = lst["items"][0]
    assert item["title"].startswith("成都 3 天")
    assert item["author"] == "阿茶"
    assert item["spot_count"] == 2
    assert item["is_draft"] is False
    assert item["summary"].endswith("…") or len(item["summary"]) <= 90

    # 详情：图片画廊 + 关联景点 + 首图作封面
    d = c.get(f"/api/guides/{gid}").json()
    assert d["images"] == ["http://img/1.jpg", "http://img/2.jpg"]
    assert d["cover_image"] == "http://img/1.jpg"
    assert [s["name"] for s in d["spots"]] == ["宽窄巷子", "大熊猫繁育研究基地"]
    assert d["is_owner"] is False  # 游客看详情


def test_draft_visibility(client):
    c, Session = client
    db = Session()
    author, other, admin, s1, s2 = _seed(db)
    db.close()

    gid = c.post(
        "/api/guides", json=_new_guide(is_draft=True), headers=_auth(author)
    ).json()["id"]
    # 草稿不进公开列表
    assert c.get("/api/guides").json()["total"] == 0
    # 游客看草稿 → 404；他人 → 404
    assert c.get(f"/api/guides/{gid}").status_code == 404
    assert c.get(f"/api/guides/{gid}", headers=_auth(other)).status_code == 404
    # 作者本人可见（且不自增浏览量）
    assert c.get(f"/api/guides/{gid}", headers=_auth(author)).json()["is_draft"] is True
    assert c.get(f"/api/guides/{gid}", headers=_auth(author)).json()["views"] == 0
    # 管理员可见
    assert c.get(f"/api/guides/{gid}", headers=_auth(admin)).status_code == 200
    # 个人中心能看到自己的草稿
    mine = c.get("/api/me/guides", headers=_auth(author)).json()
    assert len(mine) == 1 and mine[0]["is_draft"] is True


def test_search_and_views(client):
    c, Session = client
    db = Session()
    author, other, admin, s1, s2 = _seed(db)
    db.close()

    c.post("/api/guides", json=_new_guide(spot_ids=[s1.id]), headers=_auth(author))
    c.post(
        "/api/guides",
        json=_new_guide(
            title="西湖一日暴走", content="断桥到灵隐寺 9 公里", city="杭州", images=[]
        ),
        headers=_auth(author),
    )

    # 单关键词命中标题
    assert c.get("/api/guides?keyword=亲子").json()["total"] == 1
    # 多关键词为"与"关系
    assert c.get("/api/guides?keyword=成都 亲子").json()["total"] == 1
    assert c.get("/api/guides?keyword=成都 西湖").json()["total"] == 0
    # 关键词命中关联景点名
    assert c.get("/api/guides?keyword=宽窄巷子").json()["total"] == 1
    # 城市筛选
    assert c.get("/api/guides?city=杭州").json()["total"] == 1
    assert c.get("/api/guides?city=西安").json()["total"] == 0

    # 浏览量：他人/游客每次打开 +1，作者自己打开不计数
    gid = c.get("/api/guides?city=杭州").json()["items"][0]["id"]
    c.get(f"/api/guides/{gid}")
    c.get(f"/api/guides/{gid}", headers=_auth(other))
    assert c.get(f"/api/guides/{gid}").json()["views"] == 3
    # 作者自己打开不计数：用作者身份再查一次，仍是 3
    assert c.get(f"/api/guides/{gid}", headers=_auth(author)).json()["views"] == 3


def test_update_delete_permission(client):
    c, Session = client
    db = Session()
    author, other, admin, s1, s2 = _seed(db)
    db.close()

    gid = c.post("/api/guides", json=_new_guide(), headers=_auth(author)).json()["id"]

    # 他人改/删 → 403
    assert c.patch(f"/api/guides/{gid}", json=_new_guide(title="篡改"), headers=_auth(other)).status_code == 403
    assert c.delete(f"/api/guides/{gid}", headers=_auth(other)).status_code == 403
    # 未登录改 → 401
    assert c.patch(f"/api/guides/{gid}", json=_new_guide()).status_code == 401

    # 作者改：标题/正文/图片/关联景点整体替换
    r = c.patch(
        f"/api/guides/{gid}",
        json=_new_guide(title="改后的标题", images=["http://img/9.jpg"], spot_ids=[s2.id]),
        headers=_auth(author),
    )
    assert r.status_code == 200
    d = c.get(f"/api/guides/{gid}").json()
    assert d["title"] == "改后的标题"
    assert d["images"] == ["http://img/9.jpg"]
    assert [s["name"] for s in d["spots"]] == ["大熊猫繁育研究基地"]

    # 空标题/空正文 → 422
    assert c.patch(f"/api/guides/{gid}", json=_new_guide(title="   "), headers=_auth(author)).status_code == 422
    assert c.patch(f"/api/guides/{gid}", json=_new_guide(content="  "), headers=_auth(author)).status_code == 422

    # 管理员可删 → 204，之后查不到
    assert c.delete(f"/api/guides/{gid}", headers=_auth(admin)).status_code == 204
    assert c.get(f"/api/guides/{gid}").status_code == 404


def test_weather_adcode_resolve():
    """天气 adcode：字典命中 + 别名归一化 + 未知城市安全返回空。"""
    from app.services import weather

    assert weather.normalize_city("成都市") == "成都"
    assert weather.normalize_city("北京·朝阳区") == "北京"
    assert weather.resolve_adcode("成都") == "510100"
    assert weather.resolve_adcode("杭州市") == "330100"
    assert weather.resolve_adcode("三亚") == "460200"
    assert weather.resolve_adcode("根本不存在的城市") == ""
