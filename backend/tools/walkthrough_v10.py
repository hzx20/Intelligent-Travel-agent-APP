"""v1.0 全链路走查：用真实数据库把整站核心链路从头走一遍（不起服务、不走代理）。

覆盖：游客浏览 → 注册登录 → 收藏 → 评论 → 写攻略/草稿 → 静态地图
      → 管理后台（提权→六模块→评论软删→用户停用→恢复）→ 清理

用法：backend 目录下 python tools/walkthrough_v10.py
      （加 --with-ai 会额外跑一次真实 AI 规划，需联网调智谱，约 40 秒，默认跳过）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db.database import engine  # noqa: E402
from app.main import app  # noqa: E402

ok = fail = 0
user_id_holder = {}


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✅ {name} {extra}")
    else:
        fail += 1
        print(f"  ❌ {name} {extra}")


def promote(username):
    """直接把用户提权为管理员（等价于 tools/make_admin.py）。"""
    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET is_admin = 1 WHERE username = :u"), {"u": username})


c = TestClient(app)

print("【1】游客可浏览（未登录不该 401）")
r = c.get("/api/spots?city=成都&page_size=4")
check("景点列表", r.status_code == 200 and len(r.json()["items"]) == 4, f"total={r.json()['total']}")
check("热门推荐", c.get("/api/spots/hot").status_code == 200)
check("猜你喜欢（游客）", c.get("/api/spots/for-you-guest?page=2").json()["page"] == 2)
spot = r.json()["items"][0]
d = c.get(f"/api/spots/{spot['id']}").json()
check("景点详情", d["spot"]["name"] == spot["name"])
check("游客详情 favorited=false", d["favorited"] is False)
check("详情带经纬度（地图用）", bool(d["spot"].get("lng") and d["spot"].get("lat")))
check("攻略列表（游客）", c.get("/api/guides").status_code == 200)
check("游客写攻略 401", c.post("/api/guides", json={"title": "x", "content": "y"}).status_code == 401)
check("游客看个人中心 401", c.get("/api/me/profile").status_code == 401)

print("\n【2】注册 → 登录 → 资料")
u = f"wt10_{int(Path(__file__).stat().st_mtime) % 100000}"
r = c.post("/api/auth/register", json={"username": u, "password": "walk123456", "nickname": "走查员"})
check("注册", r.status_code in (200, 201), r.text[:60])
user_id_holder["id"] = r.json()["id"] if r.json().get("id") else None
token = c.post("/api/auth/login", json={"username": u, "password": "walk123456"}).json()["token"]
H = {"Authorization": f"Bearer {token}"}
me = c.get("/api/me/profile", headers=H).json()
check("资料卡昵称", me["nickname"] == "走查员", f"id={me['id']}")
user_id_holder["id"] = me["id"]
check("改昵称", c.patch("/api/me/profile", headers=H, json={"nickname": "走查员2"}).json()["nickname"] == "走查员2")
check("空昵称被拒 422", c.patch("/api/me/profile", headers=H, json={"nickname": "  "}).status_code == 422)
check("错误密码登录失败", c.post("/api/auth/login", json={"username": u, "password": "wrong"}).status_code in (400, 401))

print("\n【3】收藏 → 取消收藏")
f1 = c.post(f"/api/spots/{spot['id']}/favorite", headers=H).json()
check("收藏成功", f1["favorited"] is True and f1["favorite_count"] >= 1)
check("详情变已收藏", c.get(f"/api/spots/{spot['id']}", headers=H).json()["favorited"] is True)
check("我的收藏列表", any(x["spot"]["id"] == spot["id"] for x in c.get("/api/me/favorites", headers=H).json()))
f2 = c.post(f"/api/spots/{spot['id']}/favorite", headers=H).json()
check("再点取消收藏", f2["favorited"] is False)
c.post(f"/api/spots/{spot['id']}/favorite", headers=H)  # 收回来，留作后台查看用

print("\n【4】评论 → 软删")
cm = c.post(f"/api/spots/{spot['id']}/comments", headers=H, json={"content": "走查留痕：值得一去", "rating": 5}).json()
check("发评论", cm["content"] == "走查留痕：值得一去" and cm["rating"] == 5)
check("详情含我的评论", any(x["id"] == cm["id"] for x in c.get(f"/api/spots/{spot['id']}").json()["comments"]))
check("空评论被拒 422", c.post(f"/api/spots/{spot['id']}/comments", headers=H, json={"content": "  "}).status_code == 422)
check("超长评论被拒 422", c.post(f"/api/spots/{spot['id']}/comments", headers=H, json={"content": "字" * 501}).status_code == 422)

print("\n【5】攻略：发布 → 检索 → 草稿 → 编辑")
spots2 = c.get("/api/spots?city=成都&page_size=2").json()["items"]
imgs = [s["image_url"] for s in spots2 if s.get("image_url")]
g = c.post("/api/guides", json={
    "title": "走查：成都两天一夜", "content": "第一天：宽窄巷子、人民公园。\n第二天：熊猫基地。",
    "city": "成都", "images": imgs, "spot_ids": [s["id"] for s in spots2],
}, headers=H).json()
gid = g["id"]
check("发布攻略", g.get("id") is not None)
check("列表可查", any(x["id"] == gid for x in c.get("/api/guides").json()["items"]))
check("关键词检索", c.get("/api/guides?keyword=走查 熊猫").json()["total"] >= 1)
check("无关词不命中", c.get("/api/guides?keyword=走查 马尔代夫").json()["total"] == 0)
check("城市筛选", c.get("/api/guides?city=杭州").json()["total"] == 0)
check("详情含图片与关联景点", len(c.get(f"/api/guides/{gid}").json()["images"]) == len(imgs))
check("浏览量递增", c.get(f"/api/guides/{gid}").json()["views"] >= 1)
check("他人改攻略 403", c.patch(f"/api/guides/{gid}", json={"title": "x", "content": "y"}).status_code == 401)
c.patch(f"/api/guides/{gid}", headers=H, json={
    "title": "走查：改后标题", "content": "改后正文", "city": "成都",
    "images": imgs[:1], "spot_ids": [spots2[0]["id"]], "is_draft": True,
})
check("转草稿后公开列表不可见", all(x["id"] != gid for x in c.get("/api/guides").json()["items"]))
check("作者仍可见草稿", c.get(f"/api/guides/{gid}", headers=H).status_code == 200)
c.patch(f"/api/guides/{gid}", headers=H, json={
    "title": "走查：成都两天一夜", "content": "第一天：宽窄巷子、人民公园。\n第二天：熊猫基地。",
    "city": "成都", "images": imgs, "spot_ids": [s["id"] for s in spots2], "is_draft": False,
})
check("重新发布可见", any(x["id"] == gid for x in c.get("/api/guides").json()["items"]))

print("\n【6】静态地图")
pts = ";".join(f"{s['lng']},{s['lat']}" for s in spots2 if s.get("lng"))
r = c.get(f"/api/map/static?points={pts}&size=640*300&zoom=12")
check("行程路线图", r.status_code == 200 and r.headers["content-type"] == "image/png", f"{len(r.content)} bytes")
check("无坐标 400", c.get("/api/map/static?points=").status_code == 400)

print("\n【7】管理后台（先提权）")
check("提权前 403", c.get("/api/admin/users").status_code in (401, 403))
promote(u)
token2 = c.post("/api/auth/login", json={"username": u, "password": "walk123456"}).json()["token"]
HA = {"Authorization": f"Bearer {token2}"}
check("提权后可进后台", c.get("/api/admin/users", headers=HA).status_code == 200)
check("景点管理", c.get("/api/admin/spots?page_size=3", headers=HA).status_code == 200)
check("收藏记录", c.get("/api/admin/favorites", headers=HA).status_code == 200)
check("评论管理", c.get("/api/admin/comments", headers=HA).status_code == 200)
check("攻略管理", c.get("/api/admin/guides", headers=HA).status_code == 200)
check("AI 规划记录", c.get("/api/admin/ai-plans", headers=HA).status_code == 200)
check("评论软删", c.delete(f"/api/admin/comments/{cm['id']}", headers=HA).status_code in (200, 204))
check("软删后详情不含", all(x["id"] != cm["id"] for x in c.get(f"/api/spots/{spot['id']}").json()["comments"]))
# 注意：这个接口的参数走 URL 查询串（?is_active=false），不是请求体
check("禁止停用自己 400", c.patch(f"/api/admin/users/{me['id']}?is_active=false", headers=HA).status_code == 400)
check("自停用被拒后仍可用", c.get("/api/me/profile", headers=HA).status_code == 200)

print("\n【8】清理")
check("删攻略", c.delete(f"/api/guides/{gid}", headers=H).status_code == 204)
with engine.begin() as conn:
    conn.execute(text("DELETE FROM favorites WHERE user_id = :i"), {"i": me["id"]})
    conn.execute(text("DELETE FROM comments WHERE user_id = :i"), {"i": me["id"]})
    conn.execute(text("DELETE FROM users WHERE username = :u"), {"u": u})
    left = conn.execute(text("SELECT COUNT(*) FROM users WHERE username = :u"), {"u": u}).scalar()
    check("临时用户已删除", left == 0)
    left_g = conn.execute(text("SELECT COUNT(*) FROM guides WHERE title LIKE '走查%'")).scalar()
    check("无残留攻略", left_g == 0, f"残留 {left_g} 条")

if "--with-ai" in sys.argv:
    print("\n【9】AI 规划（真实调用智谱，约 40 秒）")
    try:
        from app.services.graph import plan_graph
        import asyncio
        state = {"history": [], "user_input": "成都 3 天，带 5 岁孩子，轻松一点",
                 "collected": {}, "itinerary": None, "weather": {}, "verify_logs": [], "logs": [], "done": False}
        out = asyncio.run(plan_graph.ainvoke(state))
        days = (out.get("itinerary") or {}).get("days") or []
        check("行程生成", len(days) > 0, f"{len(days)} 天")
        check("天气查询", bool(out.get("weather", {}).get("ok")))
    except Exception as e:  # noqa: BLE001
        check("AI 规划未通过", False, str(e)[:120])
else:
    print("\n【9】AI 规划：本次跳过（加 --with-ai 可跑真实调用，约 40 秒）")

print(f"\n{'='*50}\n走查结果：{ok} 项通过 / {fail} 项失败\n{'='*50}")
sys.exit(1 if fail else 0)
