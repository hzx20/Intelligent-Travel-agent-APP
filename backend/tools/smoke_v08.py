"""v0.8 攻略板块真库冒烟：直接用 TestClient 打真实 app.db（不起服务、不走代理）。

覆盖链路：老库补列 → 注册 → 取真实景点 → 发攻略（含图片/关联景点）
        → 列表/搜索/详情浏览计数 → 编辑 → 草稿可见性 → 删除清理
用法：backend 目录下 python tools/smoke_v08.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import inspect, text  # noqa: E402

from app.db.database import DATABASE_PATH, engine  # noqa: E402
from app.main import app  # noqa: E402  （import 即触发 init_db + 补列迁移）

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✅ {name} {extra}")
    else:
        fail += 1
        print(f"  ❌ {name} {extra}")


c = TestClient(app)

print("【0】老库补列（guides.views / guides.is_draft）")
cols = {c_["name"] for c_ in inspect(engine).get_columns("guides")}
check("views 列存在", "views" in cols)
check("is_draft 列存在", "is_draft" in cols)
print(f"  库文件：{DATABASE_PATH}")

print("\n【1】注册用户 + 取真实景点")
u = f"smoke08_{Path(__file__).stat().st_mtime_ns % 100000}"
r = c.post("/api/auth/register", json={"username": u, "password": "smoke123456"})
check("注册成功", r.status_code in (200, 201), r.text[:80])
token = c.post("/api/auth/login", json={"username": u, "password": "smoke123456"}).json()["token"]
H = {"Authorization": f"Bearer {token}"}
spots = c.get("/api/spots?city=成都&page_size=3").json()["items"]
check("取到成都真实景点", len(spots) == 3, f"{[s['name'] for s in spots]}")
spot_ids = [s["id"] for s in spots[:2]]
imgs = [s["image_url"] for s in spots if s.get("image_url")][:2]

print("\n【2】发布攻略")
r = c.post("/api/guides", json={
    "title": "冒烟：成都 3 天 2 晚亲子悠闲攻略",
    "content": "Day1 宽窄巷子 → 人民公园 → 武侯祠 → 锦里，全程顺路不折腾。\n\nDay2 熊猫基地赶 8:30 首批进场。",
    "city": "成都",
    "images": imgs,
    "spot_ids": spot_ids,
}, headers=H)
check("发布成功 201", r.status_code == 201, r.text[:120])
gid = r.json().get("id")

print("\n【3】列表 / 检索 / 详情")
lst = c.get("/api/guides?city=成都").json()
check("列表可见", any(g["id"] == gid for g in lst["items"]), f"total={lst['total']}")
check("单关键词命中标题", c.get("/api/guides?keyword=亲子").json()["total"] >= 1)
check("多关键词为与关系", c.get("/api/guides?keyword=成都 亲子").json()["total"] >= 1)
check("无关词不命中", c.get("/api/guides?keyword=成都 马尔代夫").json()["total"] == 0)
if spot_ids:
    nm = spots[0]["name"][:3]
    check("命中关联景点名", c.get(f"/api/guides?keyword={nm}").json()["total"] >= 1, nm)

d0 = c.get(f"/api/guides/{gid}").json()
check("详情图片画廊", len(d0["images"]) == len(imgs), f"{len(d0['images'])} 图")
check("关联景点回显", len(d0["spots"]) == len(spot_ids))
check("首图自动作封面", d0["cover_image"] == (imgs[0] if imgs else ""))
v1 = c.get(f"/api/guides/{gid}").json()["views"]
check("浏览量递增", v1 >= 1, f"views={v1}")

print("\n【4】编辑 / 草稿 / 权限")
r = c.patch(f"/api/guides/{gid}", json={
    "title": "冒烟：改后的标题", "content": "改过的正文。", "city": "成都",
    "images": imgs[:1], "spot_ids": spot_ids[:1], "is_draft": True,
}, headers=H)
check("编辑成功", r.status_code == 200, r.text[:80])
check("草稿不进公开列表", all(g["id"] != gid for g in c.get("/api/guides").json()["items"]))
check("游客看草稿 404", c.get(f"/api/guides/{gid}").status_code == 404)
check("作者可见草稿", c.get(f"/api/guides/{gid}", headers=H).status_code == 200)
mine = c.get("/api/me/guides", headers=H).json()
check("个人中心含草稿标记", any(g["id"] == gid and g["is_draft"] for g in mine))

print("\n【5】清理")
check("删除成功 204", c.delete(f"/api/guides/{gid}", headers=H).status_code == 204)
check("删除后 404", c.get(f"/api/guides/{gid}").status_code == 404)
with engine.begin() as conn:
    n = conn.execute(text("SELECT COUNT(*) FROM guides WHERE title LIKE '冒烟%'")).scalar()
    check("无残留冒烟数据", n == 0, f"残留 {n} 条")
    conn.execute(text("DELETE FROM users WHERE username = :u"), {"u": u})

print(f"\n{'='*46}\n冒烟结果：{ok} 项通过 / {fail} 项失败\n{'='*46}")
sys.exit(1 if fail else 0)
