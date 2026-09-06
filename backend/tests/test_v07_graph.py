"""v0.7 测试：地点核实（mock 高德）/ 天气解析 / LangGraph 图编译与流程 / SSE 接口。"""
import asyncio
import json
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.db.models import AiPlan, Spot, User  # noqa: E402
from app.security import create_token  # noqa: E402
from app.services import map_verify  # noqa: E402
from app.services.graph import plan_graph  # noqa: E402


def _seed_db(db):
    """杭州库内景点：本地命中 + 同城替代用。"""
    db.add_all([
        Spot(name="西湖", city="杭州", district="西湖区", is_free=True,
             amap_id="W001", lng=120.14, lat=30.25, address="龙井路1号"),
        Spot(name="灵隐寺", city="杭州", district="西湖区", is_free=False,
             amap_id="W002", lng=120.09, lat=30.24, address="法云弄1号"),
        Spot(name="西溪湿地", city="杭州", district="余杭区", is_free=False,
             amap_id="W003", lng=120.06, lat=30.27, address="天目山路518号"),
    ])
    db.commit()


def test_verify_db_hit_and_substitute():
    """本地命中走 db；查无此地替换为同城景点；日志齐全。"""
    from app.db.database import SessionLocal, init_db

    init_db()
    db = SessionLocal()
    try:
        db.query(Spot).filter(Spot.city == "杭州").delete()
        _seed_db(db)
        itinerary = {
            "days": [
                {"day": 1, "spots": [{"name": "西湖"}, {"name": "不存在的景点XYZ"}]},
                {"day": 2, "spots": [{"name": "灵隐寺"}]},
            ]
        }
        new_itin, logs = map_verify.verify_itinerary_spots(
            itinerary, "杭州", db, amap_enabled=False  # 关高德，纯本地链路
        )
        actions = {l["spot"]: l["action"] for l in logs}
        assert actions["西湖"] == "db"
        assert actions["不存在的景点XYZ"] == "replaced", "虚构地点应被同城替代"
        assert actions["灵隐寺"] == "db"
        d1_names = [s["name"] for s in new_itin["days"][0]["spots"]]
        assert "不存在的景点XYZ" not in d1_names, "替换后原虚构名不应残留"
        assert d1_names[1] in ("灵隐寺", "西溪湿地"), "替代景点应来自真实库"
        sub = new_itin["days"][0]["spots"][1]
        assert sub["lng"] and sub["lat"], "替代景点必须带真实坐标（供地图标点）"
    finally:
        db.close()


def test_verify_unverified_when_no_substitute():
    """库空且关高德 → 保留原名并标记 unverified（不崩）。"""
    from app.db.database import SessionLocal, init_db

    init_db()
    db = SessionLocal()
    try:
        db.query(Spot).filter(Spot.city == "杭州").delete()
        db.commit()
        new_itin, logs = map_verify.verify_itinerary_spots(
            {"days": [{"day": 1, "spots": [{"name": "神秘景点"}]}]},
            "杭州", db, amap_enabled=False,
        )
        assert logs[0]["action"] == "unverified"
        assert new_itin["days"][0]["spots"][0]["name"] == "神秘景点"
    finally:
        db.close()


def test_weather_parse_failure_safe():
    """天气接口异常时返回 ok=False 而非抛异常。"""
    from app.services import weather

    r = weather.fetch_weather("错误的adcode", "某城")
    assert r.get("ok") is False and r.get("city") == "某城"


def test_graph_compiled_and_flows():
    """图编译成功；clarify 未完成 → 条件边终止（只跑 clarify 节点）。"""
    assert plan_graph is not None
    state = {
        "history": [], "user_input": "我想去成都玩", "prev_collected": {}, "logs": [],
    }
    result = asyncio.run(plan_graph.ainvoke(state))
    assert result["done"] is False, "缺天数应追问"
    assert result["question"], "应有追问文案"
    assert any("澄清" in l for l in result["logs"]), "过程日志应含节点信息"


def test_plan_stream_sse_guest(client):
    """SSE 接口：游客完整走通 澄清追问 流（真调智谱）。"""
    c, Session = client
    resp = c.post(
        "/api/plan/stream",
        json={"history": [], "user_input": "我想去成都玩", "prev_collected": {}},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "event: log" in body and "event: result" in body and "event: done" in body
    # 解析 result 事件
    for line in body.splitlines():
        if line.startswith("data: ") and '"done"' in line:
            data = json.loads(line[6:])
            if data.get("done") is False:
                assert data["question"], "追问文案应在 result 中"
            break


def test_plan_stream_saves_history_when_logged_in(client):
    """登录用户完整规划（补齐天数）→ ai_plans 落库。"""
    c, Session = client
    db = Session()
    u = User(username=f"v07_{uuid.uuid4().hex[:6]}", password_hash="x", nickname="规划师")
    db.add(u)
    db.commit()
    token = create_token(u.id)
    db.close()

    resp = c.post(
        "/api/plan/stream",
        json={
            "history": [],
            "user_input": "去杭州玩 2 天",
            "prev_collected": {},
            "session_id": "sess-v07-1",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    saved = None
    for line in resp.text.splitlines():
        if line.startswith("data: ") and '"plan_id"' in line:
            data = json.loads(line[6:])
            if data.get("plan_id"):
                saved = data
    assert saved is not None, "登录用户完整规划应返回 plan_id"
    assert saved["itinerary"]["days"], "行程应有天数结构"
    assert any("核实" in l for l in
               [e for e in resp.text.split('{"node"') ]), "过程日志应含地图核实节点"

    db = Session()
    rec = db.query(AiPlan).filter(AiPlan.user_id == u.id).first()
    assert rec is not None and rec.status == "completed"
    intent = json.loads(rec.intent_json)
    assert intent.get("destination") == "杭州", "M1 字段名是 destination"
    db.close()
