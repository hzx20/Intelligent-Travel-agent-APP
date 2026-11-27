"""LLM token 用量统计：计量、带出、落库、聚合。

为什么用 contextvars 带出而不改 chat() 返回值：
- chat() 的签名和返回类型（str）被 30+ 个调用点依赖（澄清、行程、攻略、
  OCR 复核……），改成返回 dict 要动一整片，还要改测试断言；
- contextvars 是 Python 官方的「任务局部变量」机制：asyncio 里每个任务
  有独立上下文，chat() 在自己任务里 set 的值，await 它的调用方在同任务里
  直接 get 就能读到——等于免费附赠一个通道，调用点零改动。

三层用法：
1. 调用方想标记场景（可选）：llm_usage.set_route("itinerary")，之后本次
   上下文里的所有 chat 都带这个标签；
2. chat() 内部：每次调用后 extract_usage() 解析 token，record() 落库，
   并 set 到 last_usage 供调用方即时读取；
3. 管理端：summary() 按 SQL 聚合出日均调用量 / token 总量。

尽力而为原则：统计挂了不能影响业务。record() 任何异常只记日志不上抛。
"""
from __future__ import annotations

import contextvars
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("uvicorn.error")

# 最近一次 chat 的用量快照（本上下文内）。调用方可 get() 读，None 表示本上下文还没调过
last_usage: contextvars.ContextVar = contextvars.ContextVar("llm_last_usage", default=None)

# 调用场景标签。调用方在 await chat() 前调 set_route() 即可标记，不改签名
current_route: contextvars.ContextVar = contextvars.ContextVar("llm_current_route", default="")


def set_route(route: str) -> object:
    """给当前上下文标记调用场景（itinerary / clarify / guide ...）。返回 token 可 reset。"""
    return current_route.set(str(route or ""))


def extract_usage(resp) -> dict:
    """从 LLM 响应对象里解析 token 用量。

    langchain-openai 把智谱返回的 usage 放在 response_metadata.token_usage
    （OpenAI 格式：prompt_tokens / completion_tokens / total_tokens）；
    部分实现会填标准化的 usage_metadata（input/output/total）。
    两处都没有就返回全 0：测试用的假响应、异常分支都走这里，统计不炸。
    """
    meta = getattr(resp, "response_metadata", None) or {}
    tu = meta.get("token_usage") or {}
    if not tu:
        um = getattr(resp, "usage_metadata", None) or {}
        tu = {
            "prompt_tokens": um.get("input_tokens"),
            "completion_tokens": um.get("output_tokens"),
            "total_tokens": um.get("total_tokens"),
        }

    def _int(key: str) -> int:
        try:
            return int(tu.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    return {
        "prompt_tokens": _int("prompt_tokens"),
        "completion_tokens": _int("completion_tokens"),
        "total_tokens": _int("total_tokens"),
    }


def record(model: str, usage: dict, latency_ms: int, *, ok: bool = True, error: str = "") -> None:
    """写一行计量到 llm_usage 表，并把快照 set 到 last_usage。

    任何异常只记 warning：计量是观测项，挂了不能拖垮业务调用。
    """
    usage = usage or {}
    try:
        from app.db.database import SessionLocal
        from app.db.models import LlmUsage

        row = LlmUsage(
            ts=datetime.now().isoformat(timespec="seconds"),
            route=str(current_route.get("") or "")[:60],
            model=str(model or "")[:60],
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
            latency_ms=max(0, int(latency_ms or 0)),
            ok=1 if ok else 0,
            error=str(error or "")[:120],
        )
        with SessionLocal() as db:
            db.add(row)
            db.commit()
    except Exception:
        logger.warning("llm_usage 记录失败（不影响业务）", exc_info=True)
        return
    # Prometheus 实时计数器与 DB 账本同源递增（metrics 挂了不影响下面的快照）
    from app.core.metrics import record_llm_usage

    record_llm_usage(usage, ok)
    last_usage.set(
        {
            "model": str(model or ""),
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
            "latency_ms": max(0, int(latency_ms or 0)),
            "ok": bool(ok),
        }
    )


def summary(db, days: int = 7) -> dict:
    """按天聚合 token 用量。给 /api/usage/summary 用。

    返回：per_day（有数据的日期，按日倒序）、totals（窗口内总量）、
    avg_calls_per_day（按请求窗口天数平均，而不是按有数据的天数——
    「日均」应该反映真实日历跨度）、retrieval_cache（检索缓存命中率）。
    """
    from sqlalchemy import text

    from app.services import knowledge

    days = max(1, min(int(days or 7), 90))
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    rows = db.execute(
        text(
            "SELECT date(ts) AS day, COUNT(*) AS calls, "
            "COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(completion_tokens),0), "
            "COALESCE(SUM(total_tokens),0), "
            "COALESCE(SUM(CASE WHEN ok=1 THEN 1 ELSE 0 END),0) AS ok_calls "
            "FROM llm_usage WHERE ts >= :since "
            "GROUP BY date(ts) ORDER BY day DESC"
        ),
        {"since": since},
    ).fetchall()

    per_day = [
        {
            "day": r[0],
            "calls": int(r[1]),
            "prompt_tokens": int(r[2]),
            "completion_tokens": int(r[3]),
            "total_tokens": int(r[4]),
            "ok_calls": int(r[5]),
        }
        for r in rows
    ]
    totals = {
        "calls": sum(d["calls"] for d in per_day),
        "prompt_tokens": sum(d["prompt_tokens"] for d in per_day),
        "completion_tokens": sum(d["completion_tokens"] for d in per_day),
        "total_tokens": sum(d["total_tokens"] for d in per_day),
        "ok_calls": sum(d["ok_calls"] for d in per_day),
    }
    return {
        "days": days,
        "per_day": per_day,
        "totals": totals,
        "avg_calls_per_day": round(totals["calls"] / days, 2),
        "retrieval_cache": knowledge.cache_stats(),
    }
