"""Token 用量统计测试：解析、落库、contextvar 带出、聚合、缓存命中率。

LLM 调用全部用假 Model 替身（不打真实网络）；llm_usage 表用的是
conftest 引导的临时库，不污染真实数据。
"""
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.core import llm, llm_usage
from app.db.database import SessionLocal, engine
from app.services import knowledge


@pytest.fixture()
def _clean_usage_table():
    """每个测试前后清空 llm_usage 表，计数互不干扰。"""
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM llm_usage"))
        conn.commit()
    yield
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM llm_usage"))
        conn.commit()


@pytest.fixture()
def _fresh_chat_breaker():
    """chat 熔断器回干净状态（其他测试可能留下失败计数）。"""
    llm._chat_breaker.reset()
    yield
    llm._chat_breaker.reset()


def _usage_rows():
    with engine.connect() as conn:
        return conn.execute(text("SELECT * FROM llm_usage ORDER BY id")).fetchall()


# ---------------------------------------------------------------- 解析

def test_extract_usage_from_response_metadata():
    """OpenAI 格式：response_metadata.token_usage 优先。"""
    resp = SimpleNamespace(
        content="ok",
        response_metadata={"token_usage": {"prompt_tokens": 120, "completion_tokens": 45, "total_tokens": 165}},
    )
    assert llm_usage.extract_usage(resp) == {
        "prompt_tokens": 120, "completion_tokens": 45, "total_tokens": 165,
    }


def test_extract_usage_falls_back_to_usage_metadata():
    """langchain 标准化字段兜底：usage_metadata（input/output/total）。"""
    resp = SimpleNamespace(
        content="ok",
        usage_metadata={"input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
    )
    out = llm_usage.extract_usage(resp)
    assert out == {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}


def test_extract_usage_missing_returns_zeros():
    """测试替身/异常分支没有 usage 字段：全 0，不炸。"""
    resp = SimpleNamespace(content="ok")
    assert llm_usage.extract_usage(resp) == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    assert llm_usage.extract_usage(None) == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_extract_usage_garbage_values_are_zero():
    """字段是非数字垃圾（比如 None 或字符串）也按 0 处理。"""
    resp = SimpleNamespace(
        content="ok",
        response_metadata={"token_usage": {"prompt_tokens": "abc", "completion_tokens": None}},
    )
    assert llm_usage.extract_usage(resp)["prompt_tokens"] == 0
    assert llm_usage.extract_usage(resp)["completion_tokens"] == 0


# ---------------------------------------------------------------- 落库与带出

def test_record_writes_row_and_sets_contextvar(_clean_usage_table):
    llm_usage.record(
        "glm-4-flash",
        {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        850,
    )
    rows = _usage_rows()
    assert len(rows) == 1
    assert rows[0].model == "glm-4-flash"
    assert rows[0].total_tokens == 120
    assert rows[0].ok == 1
    snap = llm_usage.last_usage.get()
    assert snap["total_tokens"] == 120 and snap["ok"] is True


def test_record_failure_row(_clean_usage_table):
    llm_usage.record("glm-4-flash", {}, 40, ok=False, error="RuntimeError")
    row = _usage_rows()[0]
    assert row.ok == 0 and row.error == "RuntimeError" and row.total_tokens == 0


def test_record_never_raises_on_db_error(monkeypatch):
    """统计挂了不能拖垮业务：DB 不可用时静默返回（只记日志）。"""
    import app.db.database as database

    llm_usage.last_usage.set(None)  # 清掉同上下文里先前测试留下的快照

    class Boom:
        def __enter__(self):
            raise RuntimeError("db gone")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(database, "SessionLocal", Boom)
    llm_usage.record("m", {"total_tokens": 1}, 1)  # 不应抛异常
    assert llm_usage.last_usage.get() is None  # 失败路径不 set 快照


def test_route_tag_follows_call(_clean_usage_table):
    """set_route 之后本上下文里的记录都带场景标签。"""
    token = llm_usage.set_route("itinerary")
    try:
        llm_usage.record("m", {"total_tokens": 5}, 10)
        assert _usage_rows()[0].route == "itinerary"
    finally:
        llm_usage.current_route.reset(token)


# ---------------------------------------------------------------- chat() 集成

class _FakeChatModel:
    """替身 ChatOpenAI：返回带 token_usage 的响应，或抛异常。"""

    last_kwargs = {}
    behavior = "ok"

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs

    async def ainvoke(self, messages):
        if type(self).behavior == "raise":
            raise RuntimeError("provider exploded")
        return SimpleNamespace(
            content="好的",
            response_metadata={"token_usage": {"prompt_tokens": 30, "completion_tokens": 8, "total_tokens": 38}},
        )


def test_chat_records_usage_on_success(monkeypatch, _clean_usage_table, _fresh_chat_breaker):
    monkeypatch.setattr(llm, "ChatOpenAI", _FakeChatModel)
    monkeypatch.setattr(llm.settings, "zhipu_api_key", "test-key")
    _FakeChatModel.behavior = "ok"
    llm_usage.last_usage.set(None)

    # 注意契约：asyncio.run 会复制上下文，chat() 在任务内 set 的 contextvar
    # 只对「同一个任务里的调用方」可见。真实场景是路由/服务在同一个请求任务
    # 里 await chat() 后读取——这里按同样的结构验证。
    async def scenario():
        out = await llm.chat([{"role": "user", "content": "你好"}])
        return out, llm_usage.last_usage.get()

    import asyncio

    out, snap = asyncio.run(scenario())
    assert out == "好的"
    assert snap["total_tokens"] == 38 and snap["ok"] is True
    row = _usage_rows()[0]
    assert row.prompt_tokens == 30 and row.completion_tokens == 8 and row.total_tokens == 38
    assert row.ok == 1


def test_chat_records_failure_row(monkeypatch, _clean_usage_table, _fresh_chat_breaker):
    monkeypatch.setattr(llm, "ChatOpenAI", _FakeChatModel)
    monkeypatch.setattr(llm.settings, "zhipu_api_key", "test-key")
    _FakeChatModel.behavior = "raise"

    import asyncio

    with pytest.raises(RuntimeError):
        asyncio.run(llm.chat([{"role": "user", "content": "你好"}]))
    row = _usage_rows()[0]
    assert row.ok == 0 and row.error == "RuntimeError"
    assert llm._chat_breaker.snapshot()["failures"] == 1


# ---------------------------------------------------------------- 聚合与缓存

def test_summary_aggregates_per_day(_clean_usage_table):
    """插入跨两天的数据，验证按天聚合与日均。"""
    with engine.connect() as conn:
        conn.execute(text(
            "INSERT INTO llm_usage (ts, route, model, prompt_tokens, completion_tokens, total_tokens, latency_ms, ok, error) "
            "VALUES ('2026-11-26T10:00:00','itinerary','glm-4-flash',100,50,150,900,1,''),"
            "('2026-11-26T11:00:00','clarify','glm-4-flash',60,30,90,500,1,''),"
            "('2026-11-27T09:00:00','guide','glm-4-flash',0,0,0,30,0,'RuntimeError')"
        ))
        conn.commit()

    with SessionLocal() as db:
        out = llm_usage.summary(db, days=7)
    assert {d["day"] for d in out["per_day"]} == {"2026-11-26", "2026-11-27"}
    assert out["totals"]["calls"] == 3
    assert out["totals"]["total_tokens"] == 240
    assert out["totals"]["ok_calls"] == 2
    assert out["avg_calls_per_day"] == pytest.approx(3 / 7, abs=0.01)
    assert "hit_rate" in out["retrieval_cache"]


def test_summary_respects_window_days(_clean_usage_table):
    """窗口外的数据不计入。"""
    with engine.connect() as conn:
        conn.execute(text(
            "INSERT INTO llm_usage (ts, route, model, prompt_tokens, completion_tokens, total_tokens, latency_ms, ok, error) "
            "VALUES ('2020-01-01T00:00:00','','m',999,0,999,1,1,'')"
        ))
        conn.commit()
    with SessionLocal() as db:
        out = llm_usage.summary(db, days=7)
    assert out["totals"]["calls"] == 0
    assert out["per_day"] == []


def test_retrieval_cache_hit_rate_counts():
    """缓存命中率：同一 query 第二次检索应命中（计数差值 +1/+1）。"""
    row = SimpleNamespace(
        source_type="spot", source_id=1, city="成都", title="A", content="成都 火锅",
        source_url="", embedding=__import__("numpy").asarray(
            [1.0] + [0.0] * 7, dtype=__import__("numpy").float32
        ).tobytes(),
    )
    monkeypatch_rows = pytest.MonkeyPatch()
    try:
        monkeypatch_rows.setattr(knowledge, "_load_rows", lambda city: [row])
        monkeypatch_rows.setattr(knowledge, "_embed_sync", lambda texts: [[1.0] + [0.0] * 7])
        knowledge.clear_cache()
        before = knowledge.cache_stats()
        knowledge.search("成都 火锅", "成都", k=3)   # miss
        knowledge.search("成都 火锅", "成都", k=3)   # hit
        after = knowledge.cache_stats()
        assert after["misses"] - before["misses"] == 1
        assert after["hits"] - before["hits"] == 1
        assert after["hit_rate"] > 0
    finally:
        monkeypatch_rows.undo()
        knowledge.clear_cache()
