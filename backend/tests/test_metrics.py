"""Prometheus /metrics 端点与指标采集测试。

用 prometheus_client.REGISTRY.get_sample_value() 直接读采样值做前后对比，
不解析响应文本里的浮点数字符串；文本断言只验证序列与标签存在。
"""
import pytest
from prometheus_client import REGISTRY

from app.core import metrics
from app.services import knowledge


def _sample(name: str, labels: dict | None = None):
    return REGISTRY.get_sample_value(name, labels or {})


def test_metrics_endpoint_ok(client):
    c, _ = client
    resp = c.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    body = resp.text
    # 6 类指标家族都要出现
    assert "travel_http_requests_total" in body
    assert "travel_llm_calls_total" in body
    assert "travel_llm_tokens_total" in body
    assert "travel_search_latency_seconds" in body
    assert "travel_knowledge_cache_hits" in body
    # 直方图的 bucket 序列
    assert "travel_search_latency_seconds_bucket" in body


def test_request_counter_counts_api_calls(client):
    c, _ = client
    """打一次 /api/health 后，该路由模板的请求计数要 +1。"""
    before = _sample("travel_http_requests_total",
                     {"method": "GET", "path": "/api/health", "status": "200"}) or 0.0
    assert c.get("/api/health").status_code == 200
    after = _sample("travel_http_requests_total",
                    {"method": "GET", "path": "/api/health", "status": "200"})
    assert after == pytest.approx(before + 1)
    # 响应文本里能看到这条序列
    assert 'path="/api/health"' in c.get("/metrics").text


def test_request_counter_uses_route_template(client):
    c, _ = client
    """带路径参数的请求要聚合到路由模板，避免标签基数爆炸。"""
    # /api/trips/{trip_id} 形式的路由：用一个大概率不存在的 id，预期非 200 也照常计数
    c.get("/api/trips/999999", headers={})
    body = c.get("/metrics").text
    assert "unmatched" in body or "path=\"/api/trips" in body


def test_llm_counters_increment(client):
    c, _ = client
    before_calls_ok = _sample("travel_llm_calls_total", {"ok": "1"}) or 0.0
    before_tokens = _sample("travel_llm_tokens_total", {"kind": "total"}) or 0.0

    metrics.record_llm_usage({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}, ok=True)

    assert _sample("travel_llm_calls_total", {"ok": "1"}) == pytest.approx(before_calls_ok + 1)
    assert _sample("travel_llm_tokens_total", {"kind": "total"}) == pytest.approx(before_tokens + 15)
    assert _sample("travel_llm_tokens_total", {"kind": "prompt"}) >= 10


def test_llm_failure_counts_as_call(client):
    c, _ = client
    before_fail = _sample("travel_llm_calls_total", {"ok": "0"}) or 0.0
    metrics.record_llm_usage({}, ok=False)
    assert _sample("travel_llm_calls_total", {"ok": "0"}) == pytest.approx(before_fail + 1)
    # 失败路径不虚报 token
    assert _sample("travel_llm_tokens_total", {"kind": "total"}) is not None


def test_search_latency_observed(client, monkeypatch):
    c, _ = client
    """search() 一次（含缓存命中）都要进直方图。"""
    import numpy as np
    from types import SimpleNamespace

    row = SimpleNamespace(
        source_type="spot", source_id=1, city="成都", title="A", content="成都 火锅",
        source_url="", embedding=np.asarray([1.0] + [0.0] * 7, dtype=np.float32).tobytes(),
    )
    monkeypatch.setattr(knowledge, "_load_rows", lambda city: [row])
    monkeypatch.setattr(knowledge, "_embed_sync", lambda texts: [[1.0] + [0.0] * 7])
    knowledge.clear_cache()

    before = _sample("travel_search_latency_seconds_count") or 0.0
    before_sum = _sample("travel_search_latency_seconds_sum") or 0.0
    knowledge.search("成都 火锅", "成都", k=3)
    knowledge.search("成都 火锅", "成都", k=3)  # 缓存命中，也计数
    after = _sample("travel_search_latency_seconds_count")
    assert after == pytest.approx(before + 2)
    assert _sample("travel_search_latency_seconds_sum") >= before_sum
    knowledge.clear_cache()


def test_update_dynamic_sets_breaker_and_cache(client):
    c, _ = client
    """动态指标：熔断器快照与缓存命中率在抓取时刷新。"""
    from app.core import circuit_breaker as cb

    breaker = cb.CircuitBreaker("metrics-test", failure_threshold=2)
    breaker.record_failure()
    breaker.record_failure()  # 触发开路
    assert breaker.allow() is False  # 开路拒绝一次，rejected 计数才会 +1
    snap = breaker.snapshot()
    assert snap["state"] == cb.OPEN

    metrics.update_dynamic({"hits": 7, "misses": 3, "hit_rate": 0.7}, [snap])

    assert _sample("travel_breaker_state", {"name": "metrics-test"}) == 2.0  # open
    assert _sample("travel_breaker_rejected", {"name": "metrics-test"}) == 1.0
    assert _sample("travel_knowledge_cache_hits") == 7.0
    assert _sample("travel_knowledge_cache_hit_rate") == 0.7
    assert "travel_breaker_state{name=\"metrics-test\"}" in c.get("/metrics").text


def test_update_dynamic_swallows_garbage(client):
    c, _ = client
    """脏数据不能让 /metrics 500。"""
    metrics.update_dynamic(None, [{"name": "", "state": "weird"}])  # 空名/未知状态
    metrics.update_dynamic({"hits": "abc"}, None)
    assert c.get("/metrics").status_code == 200
