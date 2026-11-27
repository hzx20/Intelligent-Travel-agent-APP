"""LangGraph 智能体编排：澄清 → 资料检索 → 生成 → 核实 → 天气 五节点状态图。

- 多节点状态图：每个节点独立、可观测（过程日志随图流式产出）
- 流式：graph.stream(state) 天然逐节点产出，配合 SSE 推送打字机效果
- 检索节点（地图核实）做成可替换组件——未来接 Neo4j 只换这里
- 站内资料检索（RAG）插在澄清之后、生成之前：它只往提示词里加料，
  失败就退回普通生成，不改变「天气失败也不阻断」的既有原则
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
    references: list[dict]   # 检索到的站内资料（RAG）
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


async def retrieve_node(state: PlanState) -> dict:
    """节点 1.5：站内资料检索（RAG）。失败不阻断主流程。

    检索 query 只看偏好/人数/节奏，不看城市——城市走硬过滤，
    混进向量里反而会让「成都」这类高频词主导相似度。
    """
    from app.services import knowledge

    collected = state.get("collected") or {}
    city = collected.get("destination") or collected.get("city", "")
    parts = [
        collected.get("preferences", ""),
        collected.get("party", ""),
        collected.get("pace", ""),
    ]
    query = " ".join(str(p) for p in parts if p) or "景点 推荐"
    try:
        # search 是同步函数（SQLite + 同步 embedding 调用），必须丢进线程，
        # 否则会卡住整个 SSE 事件流
        refs = await asyncio.to_thread(knowledge.search, query, city, 5)
    except Exception:  # search 内部已兜底，这里再兜一层，防止未来改动漏抛
        refs = []
    msg = (
        f"资料检索：匹配 {len(refs)} 条站内资料"
        if refs
        else "资料检索：本次未匹配到站内资料，按常规生成"
    )
    return {"references": refs, "logs": _log(state, msg)}


def _display_references(refs) -> list[dict]:
    """把检索结果整理成前端可展示的出处列表。

    只带 id/标题/链接，不把正文塞进响应体。同一小节的多个片段合并成一行，
    但要把原始编号都记下来——行程里标的是 [1]..[5]，底部去重后若只剩 3 行，
    编号就对不上，用户点不到对应的资料。
    """
    display, slot = [], {}
    for index, ref in enumerate((refs or [])[:5], 1):
        if not isinstance(ref, dict):
            continue
        key = (ref.get("source_id"), ref.get("title") or "")
        if key in slot:
            display[slot[key]]["ref_ids"].append(index)
            continue
        slot[key] = len(display)
        display.append(
            {
                "source_id": ref.get("source_id"),
                "source_type": ref.get("source_type", ""),
                "title": ref.get("title") or "",
                "source_url": ref.get("source_url") or "",
                "ref_ids": [index],
            }
        )
    return display


async def generate_node(state: PlanState) -> dict:
    """节点2：行程生成（复用 M2 提示词与生成逻辑）。"""
    from app.services import citations, knowledge
    from app.services.itinerary import fill_thin_days, has_thin_day, plan_density

    refs = state.get("references") or []
    collected = state.get("collected") or {}
    # 澄清收集的字段名是 destination（前端快捷模块也写它），city 只是兼容旧数据的备选
    city = collected.get("destination") or collected.get("city", "")
    itinerary = await generate_itinerary(collected, references=refs)
    tail = ""
    if refs and isinstance(itinerary, dict):
        itinerary["references"] = _display_references(refs)
        # 引用落到具体安排：只在 generate 阶段先算一次，verify 会按核实后的
        # 地点名再算一次（核实可能改名或替换地点），两边都幂等。
        stats = citations.attach_citations(itinerary, refs)
        desc = citations.describe(stats)
        if desc:
            tail = f"；资料引用：{desc}"
    days = len((itinerary.get("days") or []))
    density = itinerary.get("density") if isinstance(itinerary, dict) else None
    if isinstance(itinerary, dict) and city and has_thin_day(itinerary, 3):
        # 模型只排了一两个点，就从同城景点库里补一个，别重跑两分钟模型。
        # 补的点标 filled=True，前端显示「补充推荐」，用户知道这不是 AI 自己想的。
        try:
            candidates = await asyncio.to_thread(knowledge.city_spots, city, 40)
        except Exception:
            candidates = []
        filled = fill_thin_days(itinerary, candidates, floor=3)
        if filled:
            itinerary["density"] = plan_density(itinerary, collected)
            if refs:
                # 补的点如果正好在某条资料里提过，也该标上引用
                citations.attach_citations(itinerary, refs)
            tail += f"；补充 {filled} 个同城景点"
    density = itinerary.get("density") if isinstance(itinerary, dict) else None
    if isinstance(density, dict) and density.get("thin_days"):
        # 补完还是少（城市景点池不够用），如实说，不要假装达标
        tail += f"；{len(density['thin_days'])} 天具名地点偏少"
    return {
        "itinerary": itinerary,
        "logs": _log(state, f"行程生成：{city} {days} 天框架完成{tail}"),
    }


async def verify_node(state: PlanState) -> dict:
    """节点3：地图核实。无法唯一确认时保留原安排供核对。"""
    from app.db.database import SessionLocal
    from app.services import citations

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
        # 核实可能替换或改名地点，按最终名字重算一次引用（幂等，先清后写）
        citations.attach_citations(new_itinerary, state.get("references") or [])
        # 密度也要重算：核实阶段会合并重复地点，生成时数出来的「每天 3 个点」
        # 合并后可能只剩 2 个，不重算日志就会虚报
        from app.services.itinerary import plan_density

        new_itinerary["density"] = plan_density(new_itinerary, state.get("collected") or {})
        # 按"天"去重，不能直接数日志条数：同一天可能产生两条去重记录
        # （核实前按名字合并一次、核实后按 POI 编号再合并一次），
        # 数条数会出现"2 天行程却说合并了 3 天"这种明显不实的提示。
        dedupe_days = {l.get("spot") for l in logs if l["action"] == "dedupe"}
        # 过检数量按"实际保留下来的地点数"算：logs 里还混着去重记录，不能拿 len(logs) 当地点数
        total = sum(len(d.get("spots") or []) for d in new_itinerary.get("days") or [])
        unverified = sum(1 for l in logs if l["action"] == "unverified")
        matched = sum(1 for day in new_itinerary.get('days', []) for spot in day.get('spots', [])
                      if spot.get('verification_status') == 'verified')
        tail = ""
        if unverified:
            tail += f"，{unverified} 个地点未能核实"
        if dedupe_days:
            tail += f"，合并 {len(dedupe_days)} 天里的重复地点"

        # 核实会把重复地点合并掉：生成时补到每天 3 个，合并后可能又掉回 2 个。
        # 不补的话，日志说"补充过"，用户看到的还是稀稀拉拉两天。补的点标 unverified，
        # 前端显示「地点待核实」——它们确实没经过地图核实，不能假装核实过。
        from app.services import knowledge
        from app.services.itinerary import fill_thin_days, has_thin_day

        if city and has_thin_day(new_itinerary, 3):
            try:
                candidates = await asyncio.to_thread(knowledge.city_spots, city, 40)
            except Exception:
                candidates = []
            filled = fill_thin_days(new_itinerary, candidates, floor=3)
            if filled:
                for spot in (s for d in new_itinerary.get("days") or [] for s in (d.get("spots") or [])):
                    if isinstance(spot, dict) and spot.get("filled") and not spot.get("verification_status"):
                        spot["verification_status"] = "unverified"
                        spot["verification_note"] = "补充推荐，位置未核实"
                new_itinerary["density"] = plan_density(new_itinerary, state.get("collected") or {})
                citations.attach_citations(new_itinerary, state.get("references") or [])
                total = sum(len(d.get("spots") or []) for d in new_itinerary.get("days") or [])
                tail += f"，补充 {filled} 个同城景点"
        return {
            "itinerary": new_itinerary,
            "verify_logs": logs,
            "logs": _log(state, f"地图核实：{total} 个安排中，{matched} 个地点位置已匹配" + tail),
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
    g.add_node("retrieve", retrieve_node)
    g.add_node("generate", generate_node)
    g.add_node("verify", verify_node)
    g.add_node("weather", weather_node)
    g.set_entry_point("clarify")
    # need_generate 的函数体一字不动，只改映射表的 value：需求不全仍然 END
    # （否则用户只说"我想去成都"就会被直接排行程，跳过追问）
    g.add_conditional_edges("clarify", need_generate, {"generate": "retrieve", END: END})
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", "verify")
    g.add_edge("verify", "weather")
    g.add_edge("weather", END)
    return g.compile()


plan_graph = build_plan_graph()
