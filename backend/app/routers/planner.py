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


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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
            # graph.stream 逐节点产出更新；每个节点开始/结束推一条过程日志
            async for chunk in graph.astream(state):
                for node_name, updates in chunk.items():
                    for log in updates.get("logs") or []:
                        yield _sse("log", {"node": node_name, "message": log})
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
            # 登录用户：需求收集完整且生成了行程 → 落库 ai_plans
            if user is not None and done and final.get("itinerary"):
                record = AiPlan(
                    user_id=user.id,
                    session_id=body.session_id or "",
                    status="completed",
                    intent_json=json.dumps(final.get("collected") or {}, ensure_ascii=False),
                    process_json=json.dumps(final.get("verify_logs") or [], ensure_ascii=False),
                    result_json=json.dumps(final.get("itinerary") or {}, ensure_ascii=False),
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
