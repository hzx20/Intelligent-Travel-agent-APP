"""Prometheus 抓取端点：GET /metrics（2026-11-27 新增）。

不做鉴权、不查数据库：指标全部来自进程内存。部署约定是 Nginx 只把 /api/*
代理到公网，/metrics 只在本机（127.0.0.1）可见，正好符合 Prometheus
本机抓取的惯例；如果要远程抓取，应走内网或加访问控制，不要开到公网。
"""
from fastapi import APIRouter, Response

from app.core import metrics
from app.core.circuit_breaker import all_snapshots
from app.services import knowledge

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
def prometheus_metrics() -> Response:
    """输出 Prometheus 文本格式指标。

    先刷新动态指标（缓存命中、熔断状态）再渲染，保证每次抓取拿到的是
    当下最新快照。
    """
    metrics.update_dynamic(knowledge.cache_stats(), all_snapshots())
    body, content_type = metrics.render()
    return Response(content=body, media_type=content_type)
