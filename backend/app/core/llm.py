"""LLM 调用模块（LangChain 实现）。

对应关系：server/src/llm.js → backend/app/core/llm.py
- Node 手写 fetch 直连智谱 → Python 用 LangChain 的 ChatOpenAI 类
- ChatOpenAI 虽然名字带 OpenAI，但支持任意"OpenAI 兼容"端点：
  智谱开放平台官方兼容 OpenAI 协议，把 base_url 指向智谱即可
- chat() 输入输出格式与 Node 版完全一致：
  入参 [{role, content}]，返回 AI 回复文本字符串
- extract_json() 容错解析逻辑逐行对齐 Node 版
"""
import json
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.core.config import settings

# 智谱 OpenAI 兼容端点（ChatOpenAI 会自动在后面拼 /chat/completions）
ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"


def _to_lc_messages(messages):
    """把 [{role, content}] 字典列表转成 LangChain 消息对象。"""
    out = []
    for m in messages:
        role, content = m["role"], m["content"]
        if role == "system":
            out.append(SystemMessage(content=content))
        elif role == "assistant":
            out.append(AIMessage(content=content))
        else:
            out.append(HumanMessage(content=content))
    return out


def _make_llm(temperature: float, timeout_ms: int) -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.glm_model,
        api_key=settings.zhipu_api_key,
        base_url=ZHIPU_BASE_URL,
        temperature=temperature,
        timeout=timeout_ms / 1000,
        max_retries=1,
    )


async def chat(messages, temperature: float = 0.5, timeout_ms: int = 60000) -> str:
    """调用智谱 GLM 对话接口，返回 AI 回复文本。

    与 Node 版 chat() 签名一致：chat(messages, {temperature, timeoutMs})
    """
    llm = _make_llm(temperature, timeout_ms)
    resp = await llm.ainvoke(_to_lc_messages(messages))
    content = resp.content
    if not content:
        raise RuntimeError("智谱API返回异常：无 content 字段")
    return content


def extract_json(text: str) -> dict:
    """从 AI 回复中容错提取 JSON 对象（剥掉 ```json 包裹、截取首尾大括号）。

    逐行对齐 Node 版 extractJson()。
    """
    t = str(text).strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", t)
    if fence:
        t = fence.group(1).strip()
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise RuntimeError("AI 未返回合法 JSON：" + t[:120])
    return json.loads(t[start : end + 1])
