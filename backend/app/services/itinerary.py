"""M2 行程生成模块（LangChain 版）。

对应关系：server/src/itinerary.js → backend/app/services/itinerary.py
- SYSTEM_PROMPT 原文照搬，保证 AI 行为一致
- 天数不符自动重排一次的自检纠偏逻辑保持不变
- 输出：{ title, summary, days[], budget_summary, assumptions }
  POI 名称为 AI 建议的真实知名地点，M3 模块会用地图 API 逐一核实
"""
import json

from app.core.llm import chat, extract_json

SYSTEM_PROMPT = """你是资深旅行行程设计师。根据用户需求生成行程，输出纯 JSON（不要任何多余文字）：
{
  "title": "行程标题（10字内）",
  "summary": "一句话总览",
  "days": [
    {
      "day": 1,
      "theme": "当天主题（6字内）",
      "reason": "当天这样安排的理由（1-2句：为什么选这些点、怎么贴合用户偏好）",
      "items": [
        {
          "time": "09:00",
          "activity": "做什么（一句话）",
          "poi": "具体地点名（真实存在的知名地点，供后续地图核实）",
          "type": "景点/餐饮/交通/酒店/自由活动",
          "transfer": "从上一地点怎么去（文字，第一站写 从酒店/车站出发）",
          "cost_estimate": 数字（本项预计花费，单位元，按整个出行队伍算）,
          "note": "一句话小贴士"
        }
      ]
    }
  ],
  "alternatives": [
    {"option": "方案A（本方案）：一句话概括", "pros": "优点", "cons": "缺点", "chosen": true},
    {"option": "方案B：一句话概括另一种走法", "pros": "优点", "cons": "缺点", "chosen": false}
  ],
  "decision_basis": ["基于用户需求的决策依据1", "依据2", "依据3"],
  "budget_summary": { "交通": 0, "住宿": 0, "餐饮": 0, "门票": 0, "总计": 0 },
  "assumptions": ["你做的关键假设，如：未说明出发城市，按当地出发计算"]
}

硬性约束：
1. days 数量必须等于需求天数，day 从 1 开始连续编号；
2. 每天 items 4-6 条，时间从早上到晚上合理排布，符合需求的节奏（悠闲=每天3-4个点，紧凑=5-6个点）；
3. 同一天的地点必须地理上顺路（同城市内），不要上午东郊下午西郊来回折腾；
4. cost_estimate 与 budget_summary 数额要能对上；未给预算时按中低标准估算并在 assumptions 说明；
5. poi 只写真实存在的知名地点名（如 宽窄巷子、成都大熊猫繁育基地），绝对不要编造；
6. alternatives 必须给 2 个真实可行的不同走法并对比优劣，说明为什么最终选了本方案；
7. decision_basis 要明确对应到用户说过的需求（天数/偏好/预算/节奏），不许空话；
8. 只输出 JSON。"""


def _to_spot_days(it: dict) -> dict:
    """输出形状统一：days[].items[]（地点名在 poi）→ days[].spots[]（地点名在 name）。

    下游的 M3 地图核实、前端行程面板、历史方案都以 spots/name 为契约。
    缺了这一步会出现"行程已生成但每天是空的"（v1.0 验收抓到的真缺陷）。
    没写 poi 的条目（如纯休息）不进 spots，避免核实环节拿空名乱匹配。
    """
    for day in it.get("days") or []:
        if day.get("spots"):
            continue  # 已经是 spots 形状（如重跑/历史数据），不重复加工
        spots = []
        for item in day.get("items") or []:
            name = str(item.get("poi") or item.get("name") or "").strip()
            if not name:
                continue
            spots.append({**item, "name": name})
        day["spots"] = spots
    return it


async def generate_itinerary(collected):
    """生成行程 JSON。入参与出参结构和 Node 版一致。"""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"【用户需求】{json.dumps(collected, ensure_ascii=False)}\n请生成行程 JSON。",
        },
    ]
    reply = await chat(messages, temperature=0.7, timeout_ms=90000)
    it = extract_json(reply)

    # 结构校验：天数一致、每日有安排
    try:
        expect_days = int(collected.get("days") or 0)
    except (TypeError, ValueError):
        expect_days = 0
    days = it.get("days") if isinstance(it.get("days"), list) else None

    if expect_days > 0 and days is not None and len(days) != expect_days:
        # 天数不符 → 让模型重排一次（自检纠偏，不抛给用户）
        fix_reply = await chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"【用户需求】{json.dumps(collected, ensure_ascii=False)}\n"
                        f"【你上次的输出有问题】天数应为 {expect_days} 天，实际 {len(days)} 天。"
                        "请严格按天数重新输出 JSON。"
                    ),
                },
            ],
            temperature=0.4,
            timeout_ms=90000,
        )
        fixed = extract_json(fix_reply)
        fixed_days = fixed.get("days")
        if isinstance(fixed_days, list) and len(fixed_days) == expect_days:
            return _to_spot_days(fixed)
        raise RuntimeError(f"行程天数校验失败：应为 {expect_days} 天")

    if days is None or any(
        not isinstance(d.get("items"), list) or len(d.get("items")) == 0 for d in days
    ):
        raise RuntimeError("行程结构异常：某天没有安排")
    return _to_spot_days(it)
