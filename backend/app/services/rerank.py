"""智谱托管 rerank 接口封装：两阶段检索的第二阶段。

为什么用托管接口而不是本地 cross-encoder（2026-11-27 选型结论）：
- 智谱官方提供 rerank 端点（docs.bigmodel.cn「文本重排序」，POST /rerank），
  本项目全部模型调用本来就在智谱上（GLM + embedding-3），不新增供应商；
- 托管接口 0 内存占用。本地 bge-reranker-base ONNX int8 约 300MB，在 2G
  内存服务器上是可感知的负担，还多一个模型文件的部署与升级问题；
- 官方返回 relevance_score + usage，协议简单，失败成本低（回退混合排序）。

降级契约（与 knowledge.search 同一套设计）：
rerank 是增强项，不是主流程依赖。本模块任何异常都不向上抛——
rerank_scores() 成功返回 [(下标, 相关性分)]，失败返回 None，
调用方拿到 None 就按原混合排序走，等于 rerank 关闭。

熔断：挂在独立的 "rerank" 熔断器上（与 chat 分开计数）。智谱 rerank 挂了
不该把 chat 也熔掉，反之亦然。
"""
from __future__ import annotations

import logging
import time

import httpx

from app.core.circuit_breaker import get_breaker
from app.core.config import settings
from app.core.llm import ZHIPU_BASE_URL
from app.core.provider_budget import ProviderBusy, provider_slot

logger = logging.getLogger("uvicorn.error")

RERANK_URL = f"{ZHIPU_BASE_URL}/rerank"

# 失败重试 1 次（共 2 次尝试）。rerank 是加分项，重试多了白白拖慢检索；
# 与 embedding 的 3 次重试不同：embedding 失败连检索结果都没有，rerank 失败还有排序兜底。
_RETRY = 1
_RETRY_SLEEP = 0.4

_breaker = get_breaker("rerank")


def _parse(data: dict, expected: int) -> list[tuple[int, float]]:
    """校验并解析 rerank 响应：[(候选下标, relevance_score)]，按分数降序。

    响应格式（官方文档）：{"results": [{"index": 1, "relevance_score": 0.99, ...}], ...}
    下标越界、字段缺失都按异常处理，让上层走回退。
    """
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list) or not results:
        raise RuntimeError("rerank 响应缺少 results")
    out: list[tuple[int, float]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        idx = int(item.get("index", -1))
        score = float(item.get("relevance_score", 0.0))
        if 0 <= idx < expected:
            out.append((idx, score))
    if not out:
        raise RuntimeError("rerank 响应下标全部越界")
    out.sort(key=lambda pair: pair[1], reverse=True)
    return out


def rerank_scores(query: str, texts: list[str]) -> list[tuple[int, float]] | None:
    """调智谱 rerank 给候选文本打相关性分。

    query  用用户的原始问法（不是 expand_query 改写后的——改写是为了迁就
           embedding 的词面差异，cross-encoder 自己能读懂「带娃 ≈ 亲子」，
           塞进改写垃圾反而干扰判断）。
    texts  候选文本列表，顺序即下标。
    返回   [(下标, 分数)] 按分数降序；任何失败返回 None（调用方回退）。
    """
    try:
        query = str(query or "").strip()
        texts = [str(t or "") for t in texts]
        if not query or not texts:
            return None
        if not settings.zhipu_api_key:
            return None  # 没配 key：静默降级，与检索层的无 key 行为一致

        payload = {
            "model": settings.rerank_model,
            "query": query,
            "documents": texts,
            # 传 len(texts) 拿回全量排序：调用方要按自己的 floor 和 k 截取
            "top_n": len(texts),
        }
        timeout = httpx.Timeout(settings.rerank_timeout, connect=4.0)
        last: Exception | None = None
        for attempt in range(_RETRY + 1):
            try:
                if not _breaker.allow():
                    # 熔断打开：连试都不试。不算失败也不算成功，直接回退
                    return None
                with provider_slot("chat"):
                    # 复用 chat 的限流槽位：rerank 和 chat 都打智谱，共享节奏
                    with httpx.Client(timeout=timeout, trust_env=False) as client:
                        resp = client.post(
                            RERANK_URL,
                            headers={"Authorization": f"Bearer {settings.zhipu_api_key}"},
                            json=payload,
                        )
                    resp.raise_for_status()
                ordered = _parse(resp.json(), len(texts))
                _breaker.record_success()
                return ordered
            except ProviderBusy:
                # 本地限流满是我们自己拦的，不算服务故障：不记熔断、直接回退
                return None
            except Exception as exc:
                last = exc
                _breaker.record_failure()
                if attempt < _RETRY:
                    time.sleep(_RETRY_SLEEP * (attempt + 1))
        logger.warning(
            "rerank 调用失败（%s），回退混合排序", type(last).__name__
        )
        return None
    except Exception:
        logger.exception("rerank 模块内部异常，回退混合排序")
        return None


def breaker_snapshot() -> dict:
    """给 /metrics 用：rerank 熔断器当前状态。"""
    return _breaker.snapshot()
