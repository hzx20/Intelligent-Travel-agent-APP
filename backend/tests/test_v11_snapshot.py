"""v1.1 快照系统测试：历史快照（聊天+地图视角）的保存、归属权、还原与列表展示。

全部走内存库（conftest 的 client 夹具），不碰真实开发库。
"""
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.db.models import AiPlan, User  # noqa: E402
from app.security import create_token  # noqa: E402

ITINERARY = {
    "title": "杭州两日",
    "summary": "湖畔古刹慢游",
    "days": [
        {"day": 1, "theme": "湖畔", "spots": [{"name": "西湖", "lng": 120.14, "lat": 30.25}]},
        {"day": 2, "theme": "古刹", "spots": [{"name": "灵隐寺", "lng": 120.09, "lat": 30.24}]},
    ],
}


def _mk_user(db, name):
    u = User(username=f"{name}_{uuid.uuid4().hex[:6]}", password_hash="x", nickname=name)
    db.add(u)
    db.commit()
    return u, create_token(u.id)


def _mk_plan(db, user_id):
    p = AiPlan(
        user_id=user_id, session_id="s-t", status="completed",
        intent_json='{"destination":"杭州","days":2}',
        process_json="[]",
        result_json=json.dumps(ITINERARY, ensure_ascii=False),
        title="杭州两日", summary="湖畔古刹慢游",
    )
    db.add(p)
    db.commit()
    return p


def test_snapshot_owner_only(client):
    """快照更新：本人 200；他人 404（不暴露存在性）；游客 401。"""
    c, Session = client
    db = Session()
    u1, t1 = _mk_user(db, "快照主")
    u2, t2 = _mk_user(db, "旁人")
    p = _mk_plan(db, u1.id)
    db.close()

    assert c.patch(f"/api/plan/{p.id}/snapshot", json={"map_state": {"center": [120.1, 30.2], "zoom": 13}}).status_code == 401
    assert c.patch(f"/api/plan/{p.id}/snapshot", headers={"Authorization": f"Bearer {t2}"},
                   json={"map_state": {"center": [120.1, 30.2], "zoom": 13}}).status_code == 404
    ok = c.patch(f"/api/plan/{p.id}/snapshot", headers={"Authorization": f"Bearer {t1}"},
                 json={"map_state": {"center": [120.15, 30.27], "zoom": 14}})
    assert ok.status_code == 200 and ok.json()["ok"] is True


def test_snapshot_save_and_restore(client):
    """存聊天+地图视角 → 详情完整还原；空 body 不覆盖已有数据。"""
    c, Session = client
    db = Session()
    u, t = _mk_user(db, "还原师")
    p = _mk_plan(db, u.id)
    db.close()
    H = {"Authorization": f"Bearer {t}"}

    chat = [{"role": "user", "text": "去杭州玩 2 天"},
            {"role": "ai", "text": "已生成杭州两日…"}]
    r = c.patch(f"/api/plan/{p.id}/snapshot", headers=H,
                json={"messages": chat, "map_state": {"center": [120.15, 30.27], "zoom": 13.5}})
    assert r.status_code == 200

    detail = c.get(f"/api/plan/{p.id}", headers=H).json()
    assert detail["chat"] == chat, "聊天快照应原样返回"
    assert detail["map_state"]["zoom"] == 13.5 and detail["map_state"]["center"] == [120.15, 30.27]
    assert detail["itinerary"]["title"] == "杭州两日"

    # 只更新地图，不影响聊天；超长聊天截到最近 60 条
    c.patch(f"/api/plan/{p.id}/snapshot", headers=H,
            json={"map_state": {"center": [120.2, 30.3], "zoom": 15}})
    detail2 = c.get(f"/api/plan/{p.id}", headers=H).json()
    assert detail2["chat"] == chat
    assert detail2["map_state"]["zoom"] == 15

    long_chat = [{"role": "user", "text": f"消息{i}"} for i in range(80)]
    c.patch(f"/api/plan/{p.id}/snapshot", headers=H, json={"messages": long_chat})
    detail3 = c.get(f"/api/plan/{p.id}", headers=H).json()
    assert len(detail3["chat"]) == 60, "快照最多保留最近 60 条，防止无限膨胀"


def test_history_list_rich_fields(client):
    """历史列表：标题/摘要/地点数/时间齐全，且只看得到自己的。"""
    c, Session = client
    db = Session()
    u, t = _mk_user(db, "列表控")
    p = _mk_plan(db, u.id)
    db.close()

    items = c.get("/api/plan/history", headers={"Authorization": f"Bearer {t}"}).json()["items"]
    mine = [x for x in items if x["id"] == p.id]
    assert mine, "本人应能看到这条记录"
    row = mine[0]
    assert row["title"] == "杭州两日"
    assert row["summary"] == "湖畔古刹慢游"
    assert row["spots"] == 2, "地点数应为两天各 1 个之和"
    assert row["days"] == 2 and row["city"] == "杭州"
    assert row["created_at"]  # 时间字段非空
