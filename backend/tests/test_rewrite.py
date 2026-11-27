"""LLM 查询改写（三路对比）测试。

覆盖两层：expand_query_llm 的网络封装（mock httpx）与 search() 的
rewrite 三路编排（dict/llm/none，用假知识库验证真正送给 embedding 的
查询文本）。
"""
from types import SimpleNamespace

import numpy as np
import pytest

from app.core.config import settings
from app.services import knowledge

DIM = 8


@pytest.fixture(autouse=True)
def _fresh_rewrite_state():
    """改写熔断器与统计回零（模块级状态会跨测试泄漏）。"""
    knowledge._rewrite_breaker.reset()
    for key in knowledge._rewrite_stats:
        knowledge._rewrite_stats[key] = 0
    yield
    knowledge._rewrite_breaker.reset()


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


def _fake_httpx(monkeypatch):
    import httpx as real_httpx

    ns = SimpleNamespace(Timeout=real_httpx.Timeout, Client=_FakeClient)
    monkeypatch.setattr(knowledge, "httpx", ns)
    return _FakeClient


def _rewrite_response(text="亲子 平路 博物馆 轻松游览"):
    return _FakeResponse(json_data={
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 90, "completion_tokens": 12, "total_tokens": 102},
    })


# ---------------------------------------------------------------- expand_query_llm

def test_expand_query_llm_success(monkeypatch):
    fake = _fake_httpx(monkeypatch)
    fake.calls = []
    fake.response = _rewrite_response()
    monkeypatch.setattr(settings, "zhipu_api_key", "test-key")

    out = knowledge.expand_query_llm("带娃 不想爬山 去哪")
    assert out == "亲子 平路 博物馆 轻松游览"
    assert fake.calls[0]["model"] == settings.glm_model
    assert fake.calls[0]["messages"][1]["content"] == "带娃 不想爬山 去哪"
    assert knowledge.rewrite_stats()["calls"] == 1
    assert knowledge.rewrite_stats()["total_tokens"] == 102
    assert knowledge.rewrite_stats()["failures"] == 0


def test_expand_query_llm_cleans_model_politeness(monkeypatch):
    """模型带出引号/句号/多行：剥掉再压成单行。"""
    fake = _fake_httpx(monkeypatch)
    fake.calls = []
    fake.response = _rewrite_response("  「亲子 博物馆」。\n")
    monkeypatch.setattr(settings, "zhipu_api_key", "test-key")
    assert knowledge.expand_query_llm("q") == "亲子 博物馆"


def test_expand_query_llm_failure_returns_none(monkeypatch):
    """HTTP 500：重试后 None，失败计数 +1，不上抛。"""
    fake = _fake_httpx(monkeypatch)
    fake.calls = []
    fake.response = _FakeResponse(status_code=500)
    monkeypatch.setattr(settings, "zhipu_api_key", "test-key")

    assert knowledge.expand_query_llm("q") is None
    assert len(fake.calls) == 2  # 共 2 次尝试
    assert knowledge.rewrite_stats()["failures"] == 1
    assert knowledge.rewrite_stats()["calls"] == 0  # 失败不计成功调用量


def test_expand_query_llm_no_key_short_circuits(monkeypatch):
    fake = _fake_httpx(monkeypatch)
    fake.calls = []
    monkeypatch.setattr(settings, "zhipu_api_key", "")
    assert knowledge.expand_query_llm("q") is None
    assert fake.calls == []


def test_expand_query_llm_breaker_opens(monkeypatch):
    """连续失败到阈值后熔断：后续调用直接 None，不再发请求。"""
    fake = _fake_httpx(monkeypatch)
    fake.calls = []
    fake.response = _FakeResponse(status_code=500)
    monkeypatch.setattr(settings, "zhipu_api_key", "test-key")
    knowledge._rewrite_breaker.failure_threshold = 2

    knowledge.expand_query_llm("q")  # 2 次尝试
    knowledge.expand_query_llm("q")  # 2 次尝试 → 连续失败 4 > 2，开路
    calls_after = len(fake.calls)
    assert knowledge.expand_query_llm("q") is None
    assert len(fake.calls) == calls_after  # 熔断中零请求
    knowledge._rewrite_breaker.failure_threshold = 5


# ---------------------------------------------------------------- search() 三路编排

def _fake_kb(monkeypatch):
    """一行假知识库 + 可捕获 embedding 输入的替身。"""
    captured = []
    row = SimpleNamespace(
        source_type="spot", source_id=1, city="成都", title="A",
        content="成都 博物馆 亲子", source_url="",
        embedding=np.asarray([1.0] + [0.0] * (DIM - 1), dtype=np.float32).tobytes(),
    )
    monkeypatch.setattr(knowledge, "_load_rows", lambda city: [row])

    def fake_embed(texts):
        captured.append(texts[0])
        return [[1.0] + [0.0] * (DIM - 1)]

    monkeypatch.setattr(knowledge, "_embed_sync", fake_embed)
    knowledge.clear_cache()
    return captured


def test_search_rewrite_none_uses_raw_query(monkeypatch):
    captured = _fake_kb(monkeypatch)
    monkeypatch.setattr(settings, "zhipu_api_key", "test-key")
    knowledge.search("带娃 不想爬山", "成都", k=3, rewrite="none")
    assert captured == ["带娃 不想爬山"]


def test_search_rewrite_dict_expands_query(monkeypatch):
    """dict 模式（默认）：否定句替换 + 同义词追加，与历史行为一致。"""
    captured = _fake_kb(monkeypatch)
    monkeypatch.setattr(settings, "zhipu_api_key", "test-key")
    knowledge.search("带娃 不想爬山", "成都", k=3, rewrite="dict")
    sent = captured[0]
    assert "平路" in sent and "缆车" in sent  # 否定句替换生效
    assert "亲子" in sent                     # 同义词追加生效


def test_search_rewrite_dict_is_default(monkeypatch):
    """不传 rewrite 参数时行为等于 dict 模式。"""
    captured = _fake_kb(monkeypatch)
    monkeypatch.setattr(settings, "zhipu_api_key", "test-key")
    knowledge.search("带娃 不想爬山", "成都", k=3)
    assert "亲子" in captured[0]


def test_search_rewrite_llm_uses_llm_output(monkeypatch):
    captured = _fake_kb(monkeypatch)
    monkeypatch.setattr(settings, "zhipu_api_key", "test-key")
    monkeypatch.setattr(knowledge, "expand_query_llm", lambda q: "亲子 博物馆 无障碍")
    knowledge.search("带娃 不想爬山", "成都", k=3, rewrite="llm")
    assert captured == ["亲子 博物馆 无障碍"]


def test_search_rewrite_llm_failure_falls_back_to_raw(monkeypatch):
    """llm 臂失败回退原 query（不是回退 dict）：保持三臂对比纯净。"""
    captured = _fake_kb(monkeypatch)
    monkeypatch.setattr(settings, "zhipu_api_key", "test-key")
    monkeypatch.setattr(knowledge, "expand_query_llm", lambda q: None)
    knowledge.search("带娃 不想爬山", "成都", k=3, rewrite="llm")
    assert captured == ["带娃 不想爬山"]


def test_search_rewrite_cache_separation(monkeypatch):
    """三个改写模式是三个缓存键，互不串结果。"""
    captured = _fake_kb(monkeypatch)
    monkeypatch.setattr(settings, "zhipu_api_key", "test-key")
    monkeypatch.setattr(knowledge, "expand_query_llm", lambda q: "亲子 博物馆")
    knowledge.search("带娃", "成都", k=3, rewrite="none")
    knowledge.search("带娃", "成都", k=3, rewrite="dict")
    knowledge.search("带娃", "成都", k=3, rewrite="llm")
    assert len(captured) == 3  # 三次都真的检索了（没有命中彼此的缓存）
    # 第四次同模式命中缓存，不再触发 embedding
    knowledge.search("带娃", "成都", k=3, rewrite="none")
    assert len(captured) == 3


def test_search_rewrite_garbage_mode_falls_to_dict(monkeypatch):
    """非法 rewrite 值按 dict 处理，不报错。"""
    captured = _fake_kb(monkeypatch)
    monkeypatch.setattr(settings, "zhipu_api_key", "test-key")
    knowledge.search("带娃 不想爬山", "成都", k=3, rewrite="banana")
    assert "亲子" in captured[0]
