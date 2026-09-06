"""M1 需求澄清模块（LangChain 版）。

对应关系：server/src/clarify.js → backend/app/services/clarify.py
- SYSTEM_PROMPT 原文照搬，保证 AI 行为一致
- 输入输出结构一致：
  clarify_turn(history, user_input, prev_collected) →
  { collected, missing, question, done }
"""
import json

from app.core.llm import chat, extract_json

REQUIRED = ["destination", "days"]
OPTIONAL = ["dates", "party", "budget", "pace", "preferences"]

SYSTEM_PROMPT = """你是旅行规划助手的需求收集器。根据【已收集信息】和【用户本轮输入】，合并整理出最新需求，输出纯 JSON（不要任何多余文字）：
{
  "collected": {
    "destination": "目的地城市，如 成都（不确定则省略该字段）",
    "days": 数字天数（不确定则省略）,
    "dates": "具体日期，如 10月1日-3日（没说则省略）",
    "party": "人数构成，如 2大1小（没说则省略）",
    "budget": "预算数字（元），没说则省略",
    "pace": "节奏：悠闲/紧凑/中等（没说则省略）",
    "preferences": "特殊偏好，如 带5岁小孩/爱美食/亲子（没说则省略）"
  },
  "missing": ["还缺的必需字段名，只能从 destination 和 days 里选"],
  "question": "若 missing 非空，给一句自然的中文追问，一次最多问两件事，语气友好；若齐全则为 null"
}

规则：
1. 用户本轮输入可能补充、修正之前的信息，以最新说法为准；
2. 不得编造用户没说过的信息；
3. 只输出 JSON。"""


async def clarify_turn(history, user_input, prev_collected=None):
    """一轮需求澄清。参数与返回结构与 Node 版完全一致。"""
    if prev_collected is None:
        prev_collected = {}

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *history,
        {
            "role": "user",
            "content": f"【已收集信息】{json.dumps(prev_collected, ensure_ascii=False)}\n【用户本轮输入】{user_input}",
        },
    ]
    reply = await chat(messages, temperature=0.2)
    parsed = extract_json(reply)

    collected = {**prev_collected, **(parsed.get("collected") or {})}
    raw_missing = parsed.get("missing")
    if not isinstance(raw_missing, list):
        raw_missing = []
    missing = [
        k
        for k in raw_missing
        if k in REQUIRED and (k not in collected or collected[k] is None or collected[k] == "")
    ]
    done = len(missing) == 0

    return {
        "collected": collected,
        "missing": missing,
        "question": None
        if done
        else (parsed.get("question") or f"还差一点信息：请告诉我{'和'.join(missing)}？"),
        "done": done,
    }


CLARIFY_OPTIONAL_FIELDS = OPTIONAL
