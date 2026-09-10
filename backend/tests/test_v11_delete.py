"""v1.1 历史方案删除：归属权、软删后不可见、撤销恢复、误删防护。

全部走内存库（conftest 的 client 夹具），不碰真实开发库。
"""
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.db.models import AiPlan, User  # noqa: E402
from app.security import create_token  # noqa: E402

ITINERARY = {"title": "西安三日", "summary": "古都慢游", "days": [{"day": 1, "theme": "城墙", "spots": [{"name": "兵马俑", "lng": 109.27, "lat": 34.38}]}]}


def _mk_user(db, name):
    u = User(username=f"{name}_{uuid.uuid4().hex[:6]}", password_hash="x", nickname=name)
    db.add(u)
    db.commit()
    return u, create_token(u.id)


def _mk_plan(db, user_id, title="西安三日"):
    p = AiPlan(
        user_id=user_id, session_id="s-t", status="completed",
        intent_json='{"destination":"西安","days":3}',
        process_json="[]",
        result_json=json.dumps(ITINERARY, ensure_ascii=False),
        chat_json=json.dumps([{"role": "user", "text": "去西安玩3天"}], ensure_ascii=False),
        title=title, summary="古都慢游",
    )
    db.add(p)
    db.commit()
    return p


def test_delete_owner_only(client):
    """删除：本人 200；他人 404（不暴露存在性）；游客 401。"""
    c, Session = client
    db = Session()
    u1, t1 = _mk_user(db, "删除者")
    u2, t2 = _mk_user(db, "旁人")
    p = _mk_plan(db, u1.id)
    other = _mk_plan(db, u2.id)
    db.close()

    assert c.delete(f"/api/plan/{p.id}").status_code == 401
    assert c.delete(f"/api/plan/{p.id}", headers={"Authorization": f"Bearer {t2}"}).status_code == 404
    # 别人的记录不能被删掉（权限挡住的应该是 404，而不是把别人数据删了）
    assert c.get(f"/api/plan/{other.id}", headers={"Authorization": f"Bearer {t2}"}).status_code == 200

    r = c.delete(f"/api/plan/{p.id}", headers={"Authorization": f"Bearer {t1}"})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_deleted_gone_from_list_and_detail(client):
    """软删后：列表查不到、详情 404、快照也不再接受写回。"""
    c, Session = client
    db = Session()
    u, t = _mk_user(db, "消失术")
    p = _mk_plan(db, u.id)
    keep = _mk_plan(db, u.id, title="保留的那条")
    db.close()
    H = {"Authorization": f"Bearer {t}"}

    assert c.delete(f"/api/plan/{p.id}", headers=H).status_code == 200
    ids = [x["id"] for x in c.get("/api/plan/history", headers=H).json()["items"]]
    assert p.id not in ids, "删除后不应出现在历史列表"
    assert keep.id in ids, "其他记录不受影响"
    assert c.get(f"/api/plan/{p.id}", headers=H).status_code == 404
    assert c.patch(f"/api/plan/{p.id}/snapshot", headers=H,
                   json={"map_state": {"center": [1, 1], "zoom": 5}}).status_code == 404


def test_restore_undo(client):
    """撤销删除：恢复后列表与详情都能重新看到，内容原样还在。"""
    c, Session = client
    db = Session()
    u, t = _mk_user(db, "后悔药")
    p = _mk_plan(db, u.id)
    db.close()
    H = {"Authorization": f"Bearer {t}"}

    c.delete(f"/api/plan/{p.id}", headers=H)
    r = c.post(f"/api/plan/{p.id}/restore", headers=H)
    assert r.status_code == 200 and r.json()["ok"] is True

    ids = [x["id"] for x in c.get("/api/plan/history", headers=H).json()["items"]]
    assert p.id in ids, "撤销后应回到列表"
    detail = c.get(f"/api/plan/{p.id}", headers=H).json()
    assert detail["itinerary"]["title"] == "西安三日"
    assert detail["chat"] == [{"role": "user", "text": "去西安玩3天"}], "聊天快照应完好"


def test_restore_needs_owner(client):
    """撤销也要权限：他人 404，游客 401。"""
    c, Session = client
    db = Session()
    u1, t1 = _mk_user(db, "主")
    u2, t2 = _mk_user(db, "客")
    p = _mk_plan(db, u1.id)
    db.close()

    c.delete(f"/api/plan/{p.id}", headers={"Authorization": f"Bearer {t1}"})
    assert c.post(f"/api/plan/{p.id}/restore").status_code == 401
    assert c.post(f"/api/plan/{p.id}/restore", headers={"Authorization": f"Bearer {t2}"}).status_code == 404
