"""LangGraph 智能体编排（v0.7 核心）：澄清 → 生成 → 核实 → 天气 四节点状态图。

- 多节点状态图：每个节点独立、可观测（过程日志随图流式产出）
- 流式：graph.stream(state) 天然逐节点产出，配合 SSE 推送打字机效果
- 检索节点（地图核实）做成可替换组件——未来接 Neo4j 只换这里
"""
import asyncio
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from app.services import map_verify, weather
from app.services.clarify import clarify_turn
from app.services.itinerary import generate_itinerary


class PlanState(TypedDict, total=False):
    history: list[dict]      # 对话历史 [{role, content}]
    user_input: str          # 本轮用户输入
    prev_collected: dict     # 之前已收集的需求
    collected: dict          # 澄清后的需求
    question: str            # 未收集完整时的追问
    done: bool               # 需求是否收集完整
    itinerary: dict          # 生成的行程
    verify_logs: list[dict]  # 地点核实日志
    weather: dict            # 天气信息
    logs: list[str]          # 过程日志（前端展示"规划过程"）


def _log(state: PlanState, msg: str) -> list[str]:
    return (state.get("logs") or []) + [msg]


async def clarify_node(state: PlanState) -> dict:
    """节点1：需求澄清（复用 M1 提示词与解析逻辑）。"""
    result = await clarify_turn(
        state.get("history") or [],
        state.get("user_input") or "",
        state.get("prev_collected") or {},
    )
    updates: dict[str, Any] = {
        "collected": result.get("collected") or {},
        "question": result.get("question") or "",
        "done": bool(result.get("done")),
        "logs": _log(state, "需求澄清：解析你的目的地、天数与偏好"),
    }
    return updates


async def generate_node(state: PlanState) -> dict:
    """节点2：行程生成（复用 M2 提示词与生成逻辑）。"""
    itinerary = await generate_itinerary(state.get("collected") or {})
    days = len((itinerary.get("days") or []))
    return {
        "itinerary": itinerary,
        "logs": _log(state, f"行程生成：{state['collected'].get('city', '')} {days} 天框架完成"),
    }


async def verify_node(state: PlanState) -> dict:
    """节点3：地图核实（M3）——查无此地自动替换。检索节点可替换（未来 Neo4j）。"""
    from app.db.database import SessionLocal

    db = SessionLocal()
    try:
        city = state["collected"].get("destination") or state["collected"].get("city", "")
        new_itinerary, logs = await asyncio.to_thread(
            map_verify.verify_itinerary_spots,
            state.get("itinerary") or {},
            city,
            db,
            True,
        )
        replaced = sum(1 for l in logs if l["action"] == "replaced")
        return {
            "itinerary": new_itinerary,
            "verify_logs": logs,
            "logs": _log(
                state,
                f"地图核实：{len(logs)} 个地点过检" + (f"，替换 {replaced} 个虚构地点" if replaced else "，全部真实"),
            ),
        }
    finally:
        db.close()


async def weather_node(state: PlanState) -> dict:
    """节点4：天气查询（M4）——失败不打断主流程。"""
    city = state["collected"].get("destination") or state["collected"].get("city", "")
    w = await asyncio.to_thread(weather.fetch_weather, None, city)
    msg = f"天气查询：{city} {'成功' if w.get('ok') else '暂不可用（不影响行程）'}"
    return {"weather": w, "logs": _log(state, msg)}


def need_generate(state: PlanState) -> str:
    """条件边：需求收集完整 → 生成；否则结束本轮（等待用户补充）。"""
    return "generate" if state.get("done") else END


def build_plan_graph():
    g = StateGraph(PlanState)
    g.add_node("clarify", clarify_node)
    g.add_node("generate", generate_node)
    g.add_node("verify", verify_node)
    g.add_node("weather", weather_node)
    g.set_entry_point("clarify")
    g.add_conditional_edges("clarify", need_generate, {"generate": "generate", END: END})
    g.add_edge("generate", "verify")
    g.add_edge("verify", "weather")
    g.add_edge("weather", END)
    return g.compile()


plan_graph = build_plan_graph()
