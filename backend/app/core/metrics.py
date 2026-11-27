"""Prometheus 指标定义与采集（2026-11-27 新增）。

覆盖交接文档要求的 6 类指标：
1. 请求量        travel_http_requests_total{method,path,status}
2. LLM 调用      travel_llm_calls_total{ok}
3. token         travel_llm_tokens_total{kind=prompt|completion|total}
4. 检索延迟直方图 travel_search_latency_seconds（8 桶 + _sum + _count）
5. 熔断状态      travel_breaker_state / travel_breaker_rejected / travel_breaker_opens
6. 缓存命中      travel_knowledge_cache_hits / travel_knowledge_cache_misses
                 travel_knowledge_cache_hit_rate

设计取舍：
- 静态指标（计数器、直方图）在这里定义，业务代码按事件递增；
- 动态指标（熔断状态、缓存命中）是「读时快照」：/metrics 被抓取的那一刻
  从 circuit_breaker.all_snapshots() 与 knowledge.cache_stats() 现取现填，
  不需要在热路径上维护两份计数。
- /metrics 本身不查数据库：全部数据来自进程内存，抓取再频繁也不拖累 SQLite。
"""
from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# 1. 请求量：path 用路由模板（/api/trips/{trip_id}）而不是原始路径，
#    否则每个 trip id 都是一个新时间序列，标签基数会爆炸
REQUESTS = Counter(
    "travel_http_requests_total",
    "HTTP 请求量（按方法/路由模板/状态码）",
    ["method", "path", "status"],
)

# 2. LLM 调用：ok=1 成功 / 0 失败。与 llm_usage 表同源（record() 里递增），
#    表是持久账本，计数器是进程内实时值，服务各自用途
LLM_CALLS = Counter("travel_llm_calls_total", "LLM chat 调用次数", ["ok"])

# 3. token 用量：kind 区分输入/输出/总量
LLM_TOKENS = Counter(
    "travel_llm_tokens_total",
    "LLM token 消耗累计",
    ["kind"],
)

# 4. 检索延迟：桶覆盖毫秒级缓存命中（0.05s）到超时降级（10s）
SEARCH_LATENCY = Histogram(
    "travel_search_latency_seconds",
    "知识库检索耗时（含 embedding 与 rerank 的完整 search() 路径）",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# 5. 熔断状态：读时快照的 Gauge（见 update_dynamic）
#    state 取值：0=closed（正常）1=half_open（探测中）2=open（拒绝中）
BREAKER_STATE = Gauge(
    "travel_breaker_state",
    "熔断器状态（0=closed 1=half_open 2=open）",
    ["name"],
)
BREAKER_REJECTED = Gauge(
    "travel_breaker_rejected",
    "熔断器累计拒绝次数",
    ["name"],
)
BREAKER_OPENS = Gauge(
    "travel_breaker_opens",
    "熔断器累计开路次数",
    ["name"],
)

# 6. 缓存命中：读时快照
CACHE_HITS = Gauge("travel_knowledge_cache_hits", "检索缓存累计命中数")
CACHE_MISSES = Gauge("travel_knowledge_cache_misses", "检索缓存累计未命中数")
CACHE_HIT_RATE = Gauge("travel_knowledge_cache_hit_rate", "检索缓存命中率（0-1）")

_STATE_CODE = {"closed": 0, "half_open": 1, "open": 2}


def record_llm_usage(usage: dict, ok: bool) -> None:
    """llm_usage.record() 调用的挂钩：递增调用计数与 token 计数。

    usage 可能没有 token 数据（失败路径全 0）——失败的调用照样计次，
    token 不虚报。任何异常吞掉：指标挂了不能影响业务。
    """
    try:
        LLM_CALLS.labels(ok="1" if ok else "0").inc()
        usage = usage or {}
        for kind in ("prompt", "completion", "total"):
            value = usage.get(f"{kind}_tokens") or 0
            if value:
                LLM_TOKENS.labels(kind=kind).inc(int(value))
    except Exception:
        pass


def update_dynamic(cache: dict, breaker_snapshots: list[dict]) -> None:
    """抓取时刻刷新动态指标：缓存命中与各熔断器状态。

    出现在 snapshots 里的熔断器才会在 /metrics 有对应序列——首次调用前
    chat/rerank 熔断器尚未创建，不出现在输出里是正常的（懒创建）。
    """
    try:
        cache = cache or {}
        CACHE_HITS.set(float(cache.get("hits") or 0))
        CACHE_MISSES.set(float(cache.get("misses") or 0))
        CACHE_HIT_RATE.set(float(cache.get("hit_rate") or 0.0))
        seen = set()
        for snap in breaker_snapshots or []:
            name = str(snap.get("name") or "")
            if not name:
                continue
            seen.add(name)
            BREAKER_STATE.labels(name=name).set(_STATE_CODE.get(snap.get("state"), 0))
            BREAKER_REJECTED.labels(name=name).set(float(snap.get("rejected") or 0))
            BREAKER_OPENS.labels(name=name).set(float(snap.get("opens") or 0))
    except Exception:
        pass


def render() -> tuple[bytes, str]:
    """生成 /metrics 响应体。endpoint 负责调用 update_dynamic 后再 render。"""
    return generate_latest(), CONTENT_TYPE_LATEST
