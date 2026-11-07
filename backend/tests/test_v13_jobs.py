"""v1.3 后台任务：生成不再依赖前端连接。

这组测试守的是本次改造的核心承诺：
    建完任务之后，前端"消失"（切页面/刷新/关标签页都算），
    任务照样跑到底，回来还能把过程与结果完整取回。
"""
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.db.models import AiPlan, User  # noqa: E402
from app.security import create_token  # noqa: E402
from app.services import plan_jobs  # noqa: E402


FAKE_ITINERARY = {
    "title": "杭州两日",
    "summary": "湖畔古刹慢游",
    "days": [
        {"day": 1, "theme": "湖畔漫步", "spots": [{"name": "西湖", "lng": 120.14, "lat": 30.25}]},
        {"day": 2, "theme": "古刹钟声", "spots": [{"name": "灵隐寺", "lng": 120.09, "lat": 30.24}]},
    ],
}


@pytest.fixture()
def fake_graph(monkeypatch):
    """把真模型换成"慢速假图"：每步都停一下，模拟真实生成耗时。

    为什么要停：如果不 sleep，任务可能在第一个轮询之前就跑完了，
    就测不出"请求结束后任务还在后台推进"这件事。
    """

    async def fake_iter_updates(state):
        await asyncio.sleep(0.08)
        yield (
            "clarify",
            {"done": True, "collected": {"destination": "杭州", "days": 2},
             "logs": ["需求澄清：解析你的目的地、天数与偏好"]},
            ["需求澄清：解析你的目的地、天数与偏好"],
        )
        await asyncio.sleep(0.08)
        yield (
            "generate",
            {"itinerary": FAKE_ITINERARY,
             "logs": ["需求澄清：解析你的目的地、天数与偏好", "行程生成：杭州 2 天框架完成"]},
            ["行程生成：杭州 2 天框架完成"],
        )
        await asyncio.sleep(0.08)
        yield (
            "verify",
            {"verify_logs": [{"spot": "西湖", "detail": "已核实"}],
             "logs": ["需求澄清：解析你的目的地、天数与偏好",
                      "行程生成：杭州 2 天框架完成",
                      "地图核实：2 个地点过检，全部真实"]},
            ["地图核实：2 个地点过检，全部真实"],
        )

    monkeypatch.setattr(plan_jobs, "iter_updates", fake_iter_updates)
    return fake_iter_updates


def _wait_job(c, job_id, headers=None, timeout=8.0):
    """像前端那样按 seq 增量轮询，直到任务收尾。

    返回 (最后一次快照, 期间收到的全部事件)。
    """
    since = 0
    events = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = c.get(f"/api/plan/jobs/{job_id}", params={"since": since}, headers=headers or {})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        events.extend(data["events"])
        since = data["seq"]
        if data["status"] != "running":
            return data, events
        time.sleep(0.05)
    raise AssertionError(f"任务 {job_id} 超时未完成")


def test_job_returns_immediately_and_finishes_in_background(client, fake_graph):
    """建任务立刻拿到 job_id；即使前端一直不轮询，任务也会自己跑完。"""
    c, _ = client
    resp = c.post("/api/plan/jobs", json={
        "history": [], "user_input": "去杭州玩 2 天",
        "prev_collected": {}, "session_id": "s-job-1",
        "messages": [{"role": "user", "text": "去杭州玩 2 天"}],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] and body["status"] == "running", "必须立即返回，不能等生成"

    # 关键：这里刻意"不轮询"，等价于用户切到别的页面 / 关掉标签页。
    # 任务在后端继续跑，事件存在缓冲里。
    time.sleep(0.5)

    data, events = _wait_job(c, body["job_id"])
    assert data["status"] == "done"
    assert data["result"]["itinerary"]["title"] == "杭州两日"
    # 断开期间产生的事件没有丢：回来一次补齐
    kinds = [e["type"] for e in events]
    assert kinds.count("log") == 3, f"3 条过程日志应全部保留，实际 {kinds}"
    assert "result" in kinds
    # 日志去重：节点 updates 里的 logs 是累计全量，不能重复下发
    logs = [e["data"]["message"] for e in events if e["type"] == "log"]
    assert len(set(logs)) == len(logs), f"日志不应重复：{logs}"


def test_job_saves_history_for_logged_in_user(client, fake_graph):
    """登录用户的任务完成后落库：plan_id 有值，历史里能查到。"""
    c, Session = client
    db = Session()
    u = User(username=f"job_{uuid.uuid4().hex[:6]}", password_hash="x", nickname="后台生成")
    db.add(u)
    db.commit()
    uid, token = u.id, create_token(u.id)
    db.close()

    headers = {"Authorization": f"Bearer {token}"}
    resp = c.post("/api/plan/jobs", headers=headers, json={
        "history": [], "user_input": "去杭州玩 2 天",
        "prev_collected": {}, "session_id": "s-job-2",
        "messages": [{"role": "user", "text": "去杭州玩 2 天"}],
    })
    job_id = resp.json()["job_id"]
    data, _ = _wait_job(c, job_id, headers)

    assert data["status"] == "done"
    assert data["plan_id"], "登录用户的完整规划应落库并返回 plan_id"
    assert data["result"]["saved"] is True

    hist = c.get("/api/plan/history", headers=headers).json()
    assert any(h["id"] == data["plan_id"] for h in hist["items"]), "历史列表应能查到这条"

    db = Session()
    rec = db.query(AiPlan).filter(AiPlan.id == data["plan_id"]).first()
    assert rec is not None and rec.status == "completed"
    assert json.loads(rec.result_json)["title"] == "杭州两日"
    # 落库用的是任务请求里带过来的对话快照（后台任务自己开 session 写，不用请求那个）
    assert json.loads(rec.chat_json)[0]["text"] == "去杭州玩 2 天"
    db.close()


def test_active_job_can_be_found_again(client, fake_graph):
    """关掉标签页再打开（本地什么都没了）：能按用户找回还在跑的任务。"""
    c, Session = client
    db = Session()
    u = User(username=f"act_{uuid.uuid4().hex[:6]}", password_hash="x", nickname="找回")
    db.add(u)
    db.commit()
    uid, token = u.id, create_token(u.id)
    db.close()

    headers = {"Authorization": f"Bearer {token}"}
    job_id = c.post("/api/plan/jobs", headers=headers, json={
        "history": [], "user_input": "去杭州玩 2 天",
        "prev_collected": {}, "session_id": "s-job-3",
        "messages": [{"role": "user", "text": "去杭州玩 2 天"}],
    }).json()["job_id"]

    active = c.get("/api/plan/jobs/active", headers=headers).json()["job"]
    assert active is not None and active["job_id"] == job_id
    # 带原始请求回来，前端才能把对话重建出来
    assert active["request"]["user_input"] == "去杭州玩 2 天"

    _wait_job(c, job_id, headers)
    # 跑完之后不再是 active，避免用户重开页面时被旧任务"劫持"
    assert c.get("/api/plan/jobs/active", headers=headers).json()["job"] is None


def test_guest_never_gets_active_job(client, fake_graph):
    """游客共用 user_id=0 这个桶：绝不能把别人的任务当成"你的"顶上来。"""
    c, _ = client
    c.post("/api/plan/jobs", json={
        "history": [], "user_input": "去杭州玩 2 天", "prev_collected": {},
    })
    assert c.get("/api/plan/jobs/active").json()["job"] is None


def test_cancel_job_stops_generation(client, fake_graph):
    """开新会话时取消旧任务：状态变 cancelled，不会继续烧模型调用。"""
    c, _ = client
    job_id = c.post("/api/plan/jobs", json={
        "history": [], "user_input": "去杭州玩 2 天", "prev_collected": {},
    }).json()["job_id"]

    assert c.delete(f"/api/plan/jobs/{job_id}").json()["ok"] is True
    time.sleep(0.2)
    data = c.get(f"/api/plan/jobs/{job_id}").json()
    assert data["status"] == "cancelled"


def test_job_is_private_to_its_owner(client, fake_graph):
    """别人的 job_id 一律 404：任务表也要守住"只看自己的"。"""
    c, Session = client
    db = Session()
    u = User(username=f"own_{uuid.uuid4().hex[:6]}", password_hash="x", nickname="甲")
    other = User(username=f"oth_{uuid.uuid4().hex[:6]}", password_hash="x", nickname="乙")
    db.add_all([u, other])
    db.commit()
    t1, t2 = create_token(u.id), create_token(other.id)
    db.close()

    job_id = c.post("/api/plan/jobs", headers={"Authorization": f"Bearer {t1}"}, json={
        "history": [], "user_input": "去杭州玩 2 天", "prev_collected": {},
    }).json()["job_id"]

    assert c.get(f"/api/plan/jobs/{job_id}", headers={"Authorization": f"Bearer {t2}"}).status_code == 404
    assert c.get(f"/api/plan/jobs/{job_id}").status_code == 404
    assert c.get(f"/api/plan/jobs/{job_id}", headers={"Authorization": f"Bearer {t1}"}).status_code == 200


def test_stream_endpoint_still_works(client, fake_graph):
    """老 SSE 入口保留：协议不变，事件齐全。"""
    c, _ = client
    resp = c.post("/api/plan/stream", json={
        "history": [], "user_input": "去杭州玩 2 天", "prev_collected": {},
    })
    assert resp.status_code == 200
    body = resp.text
    assert "event: log" in body and "event: result" in body and "event: done" in body
