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
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.core.circuit_breaker import CircuitOpen, get_breaker
from app.core.config import settings
from app.core.llm_usage import extract_usage, record as record_usage
from app.core.provider_budget import ProviderBusy, async_provider_slot, enabled

# 智谱 OpenAI 兼容端点（ChatOpenAI 会自动在后面拼 /chat/completions）
ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"

# chat 路径的熔断器：连续 5 次失败开路、30s 冷却、半开放 1 个探测（见 circuit_breaker.py）。
# 与限流（provider_budget）互补：限流管「排队慢」，熔断管「服务挂了别再撞」。
_chat_breaker = get_breaker("chat")


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
    if not settings.zhipu_api_key:
        raise RuntimeError("缺少 ZHIPU_API_KEY，请先配置后再使用 AI 规划")
    return ChatOpenAI(
        model=settings.glm_model,
        api_key=settings.zhipu_api_key,
        base_url=ZHIPU_BASE_URL,
        temperature=temperature,
        timeout=timeout_ms / 1000,
        max_retries=0 if enabled() else 1,
    )


async def chat(messages, temperature: float = 0.5, timeout_ms: int = 60000) -> str:
    """调用智谱 GLM 对话接口，返回 AI 回复文本。

    与 Node 版 chat() 签名一致：chat(messages, {temperature, timeoutMs})

    熔断行为：连续 5 次失败后开路，开路期间直接抛 CircuitOpen（RuntimeError
    子类，调用方现有 except 兜底能接住），不再消耗 60s 超时等待；30s 后放
    1 个探测请求确认服务是否恢复。ProviderBusy（本地限流满）不算服务故障，
    不计入熔断计数。

    token 计量：成功与失败都会落一行 llm_usage（见 llm_usage.record），
    并把用量快照 set 到 llm_usage.last_usage 这个 contextvar——调用方在同
    任务里 get 即可读到，30+ 调用点的签名完全不动。
    """
    llm = _make_llm(temperature, timeout_ms)
    if not _chat_breaker.allow():
        raise CircuitOpen("chat", _chat_breaker.cooldown_remaining())
    started = time.perf_counter()
    try:
        async with async_provider_slot('chat'):
            resp = await llm.ainvoke(_to_lc_messages(messages))
        content = resp.content
        if not content:
            raise RuntimeError("智谱API返回异常：无 content 字段")
    except ProviderBusy:
        # 本地并发额度满了是我们自己拦的，不是智谱挂了——别污染熔断计数
        raise
    except BaseException as exc:
        _chat_breaker.record_failure()
        # 失败也计量（token 全 0）：失败次数本身就是排障与成本视角的一部分
        record_usage(
            settings.glm_model,
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            (time.perf_counter() - started) * 1000,
            ok=False,
            error=type(exc).__name__,
        )
        raise
    _chat_breaker.record_success()
    record_usage(settings.glm_model, extract_usage(resp), (time.perf_counter() - started) * 1000)
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
