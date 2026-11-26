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


def _mem_db():
    """独立内存库：核实测试不碰真实开发库（以前直接删真库杭州景点再补假数据，属污染）。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db.database import Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


FAKE_PLAN_ITEMS_SHAPE = {
    "budget_format": "items_and_additions_v1",
    "additional_costs": [],
    "title": "杭州两日",
    "summary": "湖畔古刹慢游",
    "days": [
        {"day": 1, "theme": "湖畔漫步", "items": [
            {"time": "09:00", "activity": "漫步湖边", "poi": "西湖", "type": "景点",
             "transfer": "从酒店出发", "cost_estimate": 0, "note": "早点去人少"},
            {"time": "14:00", "activity": "回酒店午休", "poi": "", "type": "自由活动",
             "transfer": "步行", "cost_estimate": 0, "note": ""},
            {"time": "16:00", "activity": "逛虚构景点", "poi": "不存在的景点XYZ", "type": "景点",
             "transfer": "打车", "cost_estimate": 0, "note": ""},
        ]},
        {"day": 2, "theme": "古刹钟声", "items": [
            {"time": "09:30", "activity": "礼佛", "poi": "灵隐寺", "type": "景点",
             "transfer": "公交", "cost_estimate": 75, "note": ""},
        ]},
    ],
    "budget_summary": {"总计": 75},
    "assumptions": [],
}


def test_generate_shape_regression_items_to_spots(monkeypatch):
    """回归测试（v1.0 验收抓到的真缺陷）：

    M2 生成的行程是 days[].items[]（地点名在 poi），下游核实/前端/历史都认
    days[].spots[]（地点名在 name）。缺了形状转换会出现"行程已生成但每天是空的"。
    这里用假 LLM 复现原始形状，跑通 生成 → 归一 → 核实 全链。
    """
    import asyncio

    from app.services import itinerary as itinerary_module

    async def fake_chat(messages, temperature=0.5, timeout_ms=60000):
        import json as _json
        import copy
        reply = copy.deepcopy(FAKE_PLAN_ITEMS_SHAPE)
        for day in reply['days']:
            for item in day['items']:
                item['cost_category'] = '门票' if item['type'] == '景点' else '其他'
        return _json.dumps(reply, ensure_ascii=False)

    monkeypatch.setattr(itinerary_module, "chat", fake_chat)

    collected = {"destination": "杭州", "days": 2, "preference": "安静"}
    it = asyncio.run(itinerary_module.generate_itinerary(collected))

    # 归一：每天必有非空 spots，地点名落在 name，时间等字段保留
    for day in it["days"]:
        assert day.get("spots"), f"第 {day.get('day')} 天 spots 不应为空（items 必须转成 spots）"
    d1 = it["days"][0]["spots"]
    assert [s["name"] for s in d1] == ["西湖", "不存在的景点XYZ"], "空 poi 条目应被丢弃"
    assert d1[0]["time"] == "09:00", "time 等原始字段应保留"

    # 核实：库内匹配标点，未找到的安排保留原名。
    db = _mem_db()
    try:
        _seed_db(db)
        new_itin, logs = map_verify.verify_itinerary_spots(it, "杭州", db, amap_enabled=False)
        actions = {l["spot"]: l["action"] for l in logs}
        assert actions["西湖"] == "db"
        assert actions["不存在的景点XYZ"] == "unverified"
        all_spots = [s for day in new_itin["days"] for s in day["spots"]]
        assert len(all_spots) == 3, "第2天灵隐寺也应在列"
        assert all_spots[1]['lng'] is None and all_spots[1]['lat'] is None
        assert all(s.get("lng") and s.get("lat") for s in (all_spots[0], all_spots[2]))
    finally:
        db.close()


def test_verify_db_hit_and_preserve_missing_identity():
    """本地命中走 db，查无此地保留原安排与核实状态。"""
    db = _mem_db()
    try:
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
        assert actions["不存在的景点XYZ"] == "unverified"
        assert actions["灵隐寺"] == "db"
        d1_names = [s["name"] for s in new_itin["days"][0]["spots"]]
        assert d1_names[1] == "不存在的景点XYZ"
        unresolved = new_itin["days"][0]["spots"][1]
        assert unresolved["lng"] is None and unresolved["lat"] is None
    finally:
        db.close()


def test_verify_unverified_when_no_substitute():
    """库空且关高德 → 保留原名并标记 unverified（不崩）。"""
    db = _mem_db()
    try:
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


def test_plan_stream_sse_guest(client, monkeypatch):
    """Guest clarification flows through the real graph and SSE without a provider."""
    from app.services import clarify
    c, _ = client
    calls = []

    async def reply(messages, *, temperature):
        calls.append(messages[-1]['content'])
        return json.dumps({'collected':{'destination':'成都'}, 'missing':['days'],
                           'question':'你计划在成都玩几天？'}, ensure_ascii=False)

    monkeypatch.setattr(clarify, 'chat', reply)
    resp = c.post(
        "/api/plan/stream",
        json={"history": [], "user_input": "我想去成都玩", "prev_collected": {}},
    )
    assert resp.status_code == 200
    events = [part.splitlines() for part in resp.text.strip().split('\n\n')]
    names = [part[0] for part in events]
    assert 'event: log' in names and names[-2:] == ['event: result', 'event: done']
    data = json.loads(events[-2][1].removeprefix('data: '))
    assert data['done'] is False and data['question'] == '你计划在成都玩几天？'
    assert data['collected']['destination'] == '成都' and data['plan_id'] is None
    assert len(calls) == 1 and '我想去成都玩' in calls[0]


def test_plan_history_endpoints(client):
    """历史列表与详情：仅本人可见，游客返回空列表。"""
    c, Session = client
    db = Session()
    u = User(username=f"hist_{uuid.uuid4().hex[:6]}", password_hash="x", nickname="有历史")
    other = User(username=f"oth_{uuid.uuid4().hex[:6]}", password_hash="x", nickname="别人")
    db.add_all([u, other])
    db.commit()
    db.add(AiPlan(user_id=u.id, session_id="s1", status="completed",
                  intent_json='{"destination":"西安","days":3}',
                  process_json="[]", result_json='{"days":[{"day":1,"spots":[]}]}'))
    db.commit()
    t1, t2 = create_token(u.id), create_token(other.id)
    db.close()

    # 游客 → 空列表提示
    guest = c.get("/api/plan/history").json()
    assert guest["items"] == []
    # 本人 → 1 条，含城市与天数
    mine = c.get("/api/plan/history", headers={"Authorization": f"Bearer {t1}"}).json()
    assert len(mine["items"]) == 1 and mine["items"][0]["city"] == "西安" and mine["items"][0]["days"] == 1
    pid = mine["items"][0]["id"]
    # 详情：本人可看 / 他人 404 / 游客 401
    detail = c.get(f"/api/plan/{pid}", headers={"Authorization": f"Bearer {t1}"})
    assert detail.status_code == 200 and detail.json()["intent"]["destination"] == "西安"
    assert c.get(f"/api/plan/{pid}", headers={"Authorization": f"Bearer {t2}"}).status_code == 404
    assert c.get(f"/api/plan/{pid}").status_code == 401


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
    for day in saved["itinerary"]["days"]:
        assert day.get("spots"), (
            f"第 {day.get('day')} 天 spots 不应为空——items/poi 必须转成 spots/name 再核实"
        )
    assert any("核实" in l for l in
               [e for e in resp.text.split('{"node"') ]), "过程日志应含地图核实节点"

    db = Session()
    rec = db.query(AiPlan).filter(AiPlan.user_id == u.id).first()
    assert rec is not None and rec.status == "completed"
    intent = json.loads(rec.intent_json)
    assert intent.get("destination") == "杭州", "M1 字段名是 destination"
    db.close()
