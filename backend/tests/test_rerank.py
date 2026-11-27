"""Rerank 两阶段检索测试。

两层测试对象：
1. app/services/rerank.py 的响应解析与降级行为（mock httpx，不打真实网络）；
2. knowledge.search() 的 rerank 开关集成（mock embedding/数据库/rerank 三个
   外部依赖，验证候选池、floor、回退、缓存键隔离这些编排逻辑）。

所有测试注入的都是确定性的假向量（正交基），混合分可以手算验证。
"""
from types import SimpleNamespace

import numpy as np
import pytest

from app.core import circuit_breaker as cb
from app.core.config import settings
from app.services import knowledge, rerank

DIM = 8


# ---------------------------------------------------------------- 假环境

def _vec(i: int) -> list[float]:
    """第 i 维单位向量：两两正交，余弦相似度手算可得（对角=1，其他=0）。"""
    v = [0.0] * DIM
    v[i % DIM] = 1.0
    return v


def _row(i: int, title: str = "", content: str = "") -> SimpleNamespace:
    """伪造一条 knowledge_chunks 行。embedding 直接给字节，绕开数据库。"""
    return SimpleNamespace(
        source_type="spot",
        source_id=i,
        city="成都",
        title=title,
        content=content,
        source_url="",
        embedding=np.asarray(_vec(i), dtype=np.float32).tobytes(),
    )


@pytest.fixture()
def fake_kb(monkeypatch):
    """三行假知识库：A 混合分最高（向量全中），B/C 只有关键词分（0.22，
    低于默认阈值 0.35，老路径永远返回不了它们）。

    _embed_sync 恒返回 e0：与 A 余弦=1，与 B/C 余弦=0。
    """
    rows = [
        _row(0, "A 成都火锅", "成都 火锅 推荐 龙抄手"),
        _row(1, "B 其他", "成都 火锅 其他内容"),
        _row(2, "C 其他", "成都 火锅 别的内容"),
    ]
    monkeypatch.setattr(knowledge, "_load_rows", lambda city: rows)
    monkeypatch.setattr(knowledge, "_embed_sync", lambda texts: [[1.0] + [0.0] * (DIM - 1)])
    monkeypatch.setattr(settings, "rerank_enabled", False)
    monkeypatch.setattr(settings, "rerank_pool", 10)
    monkeypatch.setattr(settings, "rerank_min_score", 0.10)
    knowledge.clear_cache()
    yield rows
    knowledge.clear_cache()


# ---------------------------------------------------------------- rerank.py 单元

class _FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._json


class _FakeClient:
    """记录请求、返回预设响应的假 httpx.Client。"""

    calls: list = []
    response: _FakeResponse = _FakeResponse()

    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, headers=None, json=None):
        type(self).calls.append(json)
        return type(self).response


class _FakeHttpxNS:
    Timeout = None  # 占位，测试里赋真实 httpx.Timeout


def test_parse_orders_by_score_desc():
    """解析要按 relevance_score 降序，越界下标剔除。"""
    data = {"results": [
        {"index": 0, "relevance_score": 0.3},
        {"index": 2, "relevance_score": 0.9},
        {"index": 9, "relevance_score": 0.99},  # 越界，剔除
        {"index": 1, "relevance_score": 0.6},
    ]}
    assert rerank._parse(data, expected=3) == [(2, 0.9), (1, 0.6), (0, 0.3)]


def test_parse_rejects_missing_results():
    with pytest.raises(RuntimeError):
        rerank._parse({}, expected=2)
    with pytest.raises(RuntimeError):
        rerank._parse({"results": [{"index": 7, "relevance_score": 1.0}]}, expected=2)


def test_rerank_scores_success(monkeypatch):
    """正常路径：返回按分降序的下标列表。"""
    ns = _FakeHttpxNS()
    import httpx as real_httpx

    ns.Timeout = real_httpx.Timeout
    ns.Client = _FakeClient
    _FakeClient.calls = []
    _FakeClient.response = _FakeResponse(json_data={
        "results": [{"index": 1, "relevance_score": 0.7}, {"index": 0, "relevance_score": 0.9}]
    })
    monkeypatch.setattr(rerank, "httpx", ns)
    monkeypatch.setattr(rerank.settings, "zhipu_api_key", "test-key")

    out = rerank.rerank_scores("成都火锅", ["甲文本", "乙文本"])
    assert out == [(0, 0.9), (1, 0.7)]
    assert _FakeClient.calls[0]["model"] == "rerank"
    assert _FakeClient.calls[0]["documents"] == ["甲文本", "乙文本"]


def test_rerank_scores_http_failure_returns_none(monkeypatch):
    """HTTP 失败：重试后返回 None，绝不上抛异常。"""
    ns = _FakeHttpxNS()
    import httpx as real_httpx

    ns.Timeout = real_httpx.Timeout
    ns.Client = _FakeClient
    _FakeClient.calls = []
    _FakeClient.response = _FakeResponse(status_code=500)
    monkeypatch.setattr(rerank, "httpx", ns)
    monkeypatch.setattr(rerank.settings, "zhipu_api_key", "test-key")
    monkeypatch.setattr(rerank, "_breaker", cb.CircuitBreaker("rerank-test"))

    assert rerank.rerank_scores("q", ["a", "b"]) is None
    assert len(_FakeClient.calls) == 2  # 重试 1 次，共 2 次尝试


def test_rerank_scores_no_key_short_circuits(monkeypatch):
    """没配 API key：直接 None，一次网络请求都不发。"""
    ns = _FakeHttpxNS()
    import httpx as real_httpx

    ns.Timeout = real_httpx.Timeout
    ns.Client = _FakeClient
    _FakeClient.calls = []
    monkeypatch.setattr(rerank, "httpx", ns)
    monkeypatch.setattr(rerank.settings, "zhipu_api_key", "")

    assert rerank.rerank_scores("q", ["a"]) is None
    assert _FakeClient.calls == []


def test_breaker_opens_after_repeated_rerank_failures(monkeypatch):
    """熔断联动：连续失败到阈值后，后续调用直接 None，不再发 HTTP 请求。"""
    ns = _FakeHttpxNS()
    import httpx as real_httpx

    ns.Timeout = real_httpx.Timeout
    ns.Client = _FakeClient
    _FakeClient.calls = []
    _FakeClient.response = _FakeResponse(status_code=500)
    monkeypatch.setattr(rerank, "httpx", ns)
    monkeypatch.setattr(rerank.settings, "zhipu_api_key", "test-key")
    monkeypatch.setattr(rerank, "_breaker", cb.CircuitBreaker("rerank-test", failure_threshold=2))

    assert rerank.rerank_scores("q", ["a", "b"]) is None  # 2 次尝试
    assert rerank.rerank_scores("q", ["a", "b"]) is None  # 2 次尝试，失败满 4 次 > 阈值 2
    calls_after_open = len(_FakeClient.calls)
    assert rerank.rerank_scores("q", ["a", "b"]) is None  # 熔断中：0 次尝试
    assert len(_FakeClient.calls) == calls_after_open


# ---------------------------------------------------------------- search() 集成

def test_default_off_keeps_legacy_behavior(fake_kb, monkeypatch):
    """rerank 未开（默认）：行为与老版本一致——阈值过滤、无 rerank_score 字段、
    rerank 接口一次都不调。"""
    called = []
    monkeypatch.setattr(knowledge, "rerank_scores", lambda *a, **kw: called.append(1) or None)

    out = knowledge.search("成都 火锅", "成都", k=3, min_score=0.35)
    assert [item["title"][0] for item in out] == ["A"]  # 只有混合分达标的 A
    assert all("rerank_score" not in item for item in out)
    assert called == []  # rerank 关闭时不该碰 rerank 接口


def test_rerank_on_reorders_and_labels(fake_kb, monkeypatch):
    """rerank 打开：按 mock 返回的相关性排序，结果带 rerank_score 字段。"""
    monkeypatch.setattr(
        knowledge, "rerank_scores",
        lambda query, docs: [(2, 0.9), (0, 0.8), (1, 0.05)],
    )
    out = knowledge.search("成都 火锅", "成都", k=3, min_score=0.35, rerank=True)
    assert [item["title"][0] for item in out] == ["C", "A"]  # C 第一，B 被 floor 滤掉
    assert out[0]["rerank_score"] == pytest.approx(0.9)
    assert out[1]["rerank_score"] == pytest.approx(0.8)


def test_rerank_pulls_below_threshold_chunk(fake_kb, monkeypatch):
    """重排的核心价值：混合分 0.22（低于 0.35 阈值）的 C 被精排捞回 Top-K。
    老路径下 C 永远进不了结果。"""
    monkeypatch.setattr(
        knowledge, "rerank_scores", lambda query, docs: [(2, 0.95), (0, 0.5), (1, 0.4)]
    )
    legacy = knowledge.search("成都 火锅", "成都", k=3, min_score=0.35, rerank=False)
    assert all(item["title"][0] != "C" for item in legacy)  # 老路径确实没有 C

    out = knowledge.search("成都 火锅", "成都", k=3, min_score=0.35, rerank=True)
    assert out[0]["title"][0] == "C"  # 精排把它拉到第一
    assert out[0]["score"] < 0.35     # 混合分仍如实标注（未造假）


def test_rerank_failure_falls_back_to_hybrid_order(fake_kb, monkeypatch):
    """rerank 失败（None）：回退候选池的混合排序，不上抛、不给空结果。"""
    monkeypatch.setattr(knowledge, "rerank_scores", lambda query, docs: None)
    out = knowledge.search("成都 火锅", "成都", k=3, min_score=0.35, rerank=True)
    assert [item["title"][0] for item in out] == ["A", "B", "C"]  # 混合分降序
    assert all("rerank_score" not in item for item in out)  # 回退路径没有精排分


def test_rerank_exception_falls_back(fake_kb, monkeypatch):
    """rerank mock 直接抛异常也要被吞掉：search 永不因 rerank 阶段报错。"""
    def boom(query, docs):
        raise RuntimeError("rerank exploded")

    monkeypatch.setattr(knowledge, "rerank_scores", boom)
    out = knowledge.search("成都 火锅", "成都", k=3, min_score=0.35, rerank=True)
    assert [item["title"][0] for item in out] == ["A", "B", "C"]


def test_rerank_floor_drops_weak_items(fake_kb, monkeypatch):
    """floor 过滤：相关性 0.05 < 0.10 的 B 被丢弃，即使它在池里。"""
    monkeypatch.setattr(
        knowledge, "rerank_scores", lambda query, docs: [(0, 0.6), (1, 0.05), (2, 0.5)]
    )
    out = knowledge.search("成都 火锅", "成都", k=3, min_score=0.35, rerank=True)
    titles = [item["title"][0] for item in out]
    assert "B" not in titles
    assert titles == ["A", "C"]


def test_all_below_floor_falls_back(fake_kb, monkeypatch):
    """全部低于 floor：保底返回混合排序头几条，结果不为空。"""
    monkeypatch.setattr(
        knowledge, "rerank_scores", lambda query, docs: [(0, 0.01), (1, 0.02), (2, 0.03)]
    )
    out = knowledge.search("成都 火锅", "成都", k=3, min_score=0.35, rerank=True)
    assert [item["title"][0] for item in out] == ["A", "B", "C"]


def test_pool_size_caps_candidates(fake_kb, monkeypatch):
    """候选池封顶 rerank_pool：传给 rerank 的文本数不超过池大小。"""
    seen_lengths = []

    def spy(query, docs):
        seen_lengths.append(len(docs))
        return [(i, 1.0 - i * 0.1) for i in range(len(docs))]

    monkeypatch.setattr(knowledge, "rerank_scores", spy)
    monkeypatch.setattr(settings, "rerank_pool", 2)
    out = knowledge.search("成都 火锅", "成都", k=2, min_score=0.35, rerank=True)
    assert seen_lengths == [2]  # 池上限生效（max(k=2, pool=2) = 2，池不会小于 k）
    assert len(out) <= 2


def test_cache_separates_rerank_on_off(fake_kb, monkeypatch):
    """同一 query 开/关重排是两个缓存键：互不串结果。"""
    monkeypatch.setattr(
        knowledge, "rerank_scores", lambda query, docs: [(2, 0.9), (0, 0.8), (1, 0.5)]
    )
    on = knowledge.search("成都 火锅", "成都", k=3, min_score=0.35, rerank=True)
    off = knowledge.search("成都 火锅", "成都", k=3, min_score=0.35, rerank=False)
    assert on[0]["title"][0] == "C" and "rerank_score" in on[0]
    assert [item["title"][0] for item in off] == ["A"] and all(
        "rerank_score" not in item for item in off
    )


def test_positional_call_still_works(fake_kb):
    """老调用姿势（全位置参数）不受新参数影响。"""
    out = knowledge.search("成都 火锅", "成都", 3, 0.35)
    assert len(out) == 1 and out[0]["title"][0] == "A"


def test_small_pool_single_candidate_skips_rerank(fake_kb, monkeypatch):
    """池里只剩 1 条时没必要花钱调 rerank。"""
    called = []

    def spy(query, docs):
        called.append(1)
        return None

    monkeypatch.setattr(knowledge, "_load_rows", lambda city: [fake_kb[0]])  # 只留一行
    monkeypatch.setattr(knowledge, "rerank_scores", spy)
    out = knowledge.search("成都 火锅", "成都", k=3, min_score=0.35, rerank=True)
    assert called == []  # 单候选短路
    assert out and out[0]["title"][0] == "A"
