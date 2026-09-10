"""规划接口（v0.7）：LangGraph 流式编排（SSE）+ 规划记录落库。

事件流（SSE）：
  event: log     节点过程日志（JSON: {message}）
  event: token   打字机文本块（JSON: {text}）—— 追问/行程说明逐字推送
  event: result  最终结构化结果（JSON: {done, question, collected, itinerary, verify_logs, weather, plan_id})
  event: done    流结束
"""
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import AiPlan, User
from app.routers.auth import get_current_user_optional
from app.services import graph as plan_graph_module

router = APIRouter(prefix="/api/plan", tags=["plan"])


class StreamIn(BaseModel):
    history: list[dict] = Field(default_factory=list)
    user_input: str = Field(min_length=1, max_length=500)
    prev_collected: dict = Field(default_factory=dict)
    session_id: str = Field(default="", max_length=50)
    messages: list[dict] = Field(default_factory=list)  # v1.1 对话快照 [{role,text,logs}]


class SnapshotIn(BaseModel):
    """v1.1 历史快照更新：聊天内容与地图视角（中心/缩放）。"""
    messages: list[dict] | None = None
    map_state: dict | None = None


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/history")
def plan_history(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_optional),
):
    """我的历史规划列表（登录用户）。"""
    if user is None:
        return {"items": [], "note": "游客不保存历史，登录后自动记录"}
    rows = (
        db.query(AiPlan)
        .filter(AiPlan.user_id == user.id, AiPlan.status == "completed")
        .order_by(AiPlan.created_at.desc())
        .limit(20)
        .all()
    )
    items = []
    for p in rows:
        intent = json.loads(p.intent_json or "{}")
        result = json.loads(p.result_json or "{}")
        spots = sum(len(d.get("spots") or []) for d in result.get("days") or [])
        items.append({
            "id": p.id,
            "session_id": p.session_id,
            "city": intent.get("destination") or intent.get("city") or "未知城市",
            "days": len(result.get("days") or []),
            "title": p.title or result.get("title") or "",
            "summary": p.summary or result.get("summary") or "",
            "spots": spots,
            "created_at": p.created_at.strftime("%Y-%m-%d %H:%M"),
        })
    return {"items": items}


@router.get("/{plan_id}")
def plan_detail(
    plan_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_optional),
):
    """单条历史规划详情（仅本人可见）。"""
    if user is None:
        from fastapi import HTTPException
        raise HTTPException(401, "登录后可查看历史规划")
    p = db.get(AiPlan, plan_id)
    if p is None or p.user_id != user.id:
        from fastapi import HTTPException
        raise HTTPException(404, "记录不存在")
    return {
        "id": p.id,
        "status": p.status,
        "intent": json.loads(p.intent_json or "{}"),
        "verify_logs": json.loads(p.process_json or "[]"),
        "itinerary": json.loads(p.result_json or "{}"),
        "chat": json.loads(p.chat_json or "[]"),
        "map_state": json.loads(p.map_json or "{}"),
        "created_at": p.created_at.strftime("%Y-%m-%d %H:%M"),
    }


def _ensure_own_plan(plan_id: int, db: Session, user) -> AiPlan:
    """取本人规划记录，不存在/不是自己的统一 404（不暴露他人记录存在性）。"""
    from fastapi import HTTPException

    p = db.get(AiPlan, plan_id)
    if p is None or p.user_id != user.id:
        raise HTTPException(404, "记录不存在")
    return p


@router.patch("/{plan_id}/snapshot")
def update_snapshot(
    plan_id: int,
    body: SnapshotIn,
    db: Session = Depends(get_db),
    user=Depends(get_current_user_optional),
):
    """v1.1：更新某条规划的对话/地图快照（仅本人）。

    前端在生成完成与地图拖动/缩放停稳后调用，把"当时的样子"持续写回，
    历史列表点击时即可原样还原。
    """
    from fastapi import HTTPException

    if user is None:
        raise HTTPException(401, "登录后可保存快照")
    p = _ensure_own_plan(plan_id, db, user)
    if body.messages is not None:
        p.chat_json = json.dumps(body.messages[-60:], ensure_ascii=False)  # 防膨胀，留最近60条
    if body.map_state is not None:
        state = {
            "center": body.map_state.get("center") or [],
            "zoom": body.map_state.get("zoom"),
        }
        p.map_json = json.dumps(state, ensure_ascii=False)
    db.commit()
    return {"ok": True}


@router.post("/stream")
async def plan_stream(
    body: StreamIn,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    """LangGraph 流式规划：游客可用（不留记录），登录用户自动保存到历史。"""
    graph = plan_graph_module.plan_graph

    async def event_stream():
        state = {
            "history": body.history,
            "user_input": body.user_input,
            "prev_collected": body.prev_collected,
            "logs": [],
        }
        final: dict = {}
        try:
            # graph.stream 逐节点产出更新；每个节点只把【新增】的过程日志推给前端
            # （节点 updates 里的 logs 是从头累计的全量，直接全发会每节点重复一遍）
            emitted = 0
            async for chunk in graph.astream(state):
                for node_name, updates in chunk.items():
                    new_logs = (updates.get("logs") or [])[emitted:]
                    for log in new_logs:
                        yield _sse("log", {"node": node_name, "message": log})
                    emitted += len(new_logs)
                    final.update(updates)
                    # 澄清未完成：把追问文本打字机式推给前端
                    if node_name == "clarify" and not updates.get("done"):
                        q = updates.get("question") or ""
                        for i in range(0, len(q), 2):
                            yield _sse("token", {"text": q[i:i + 2]})
                            import asyncio
                            await asyncio.sleep(0.03)
            done = bool(final.get("done"))
            plan_id = None
            # 登录用户：需求收集完整且生成了行程 → 落库 ai_plans（含对话快照）
            if user is not None and done and final.get("itinerary"):
                it = final.get("itinerary") or {}
                record = AiPlan(
                    user_id=user.id,
                    session_id=body.session_id or "",
                    status="completed",
                    intent_json=json.dumps(final.get("collected") or {}, ensure_ascii=False),
                    process_json=json.dumps(final.get("verify_logs") or [], ensure_ascii=False),
                    result_json=json.dumps(it, ensure_ascii=False),
                    title=str(it.get("title") or "")[:100],
                    summary=str(it.get("summary") or "")[:300],
                    chat_json=json.dumps(body.messages or [], ensure_ascii=False),
                )
                db.add(record)
                db.commit()
                db.refresh(record)
                plan_id = record.id
            yield _sse("result", {
                "done": done,
                "question": final.get("question") or "",
                "collected": final.get("collected") or {},
                "itinerary": final.get("itinerary") or {},
                "verify_logs": final.get("verify_logs") or [],
                "weather": final.get("weather") or {},
                "plan_id": plan_id,
                "saved": plan_id is not None,
            })
        except Exception as e:  # 任何节点异常都以 error 事件收尾，不静默
            yield _sse("result", {"done": False, "error": str(e)})
        yield _sse("done", {})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
