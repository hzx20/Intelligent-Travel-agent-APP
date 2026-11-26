"""RAG 检索层（knowledge.py）与编排接入（graph/itinerary）的回归测试。

不打真实接口：embedding 与数据库读取都被替换掉，跑得快、结果稳定。
真实链路的质量由 scripts/eval_retrieval.py 单独评测（Recall@3）。
"""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.services import knowledge  # noqa: E402
from app.services.graph import retrieve_node  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_cache():
    knowledge.clear_cache()
    yield
    knowledge.clear_cache()


# ---------------------------------------------------------------- 切块

def test_chunk_text_keeps_short_text_whole():
    chunks = knowledge.chunk_text("成都大熊猫基地适合带小孩，上午人少。", "熊猫基地")
    assert chunks == ["成都大熊猫基地适合带小孩，上午人少。"]


def test_chunk_text_splits_long_text_with_overlap():
    text = "。".join(f"第{i}句讲成都亲子出行的注意事项" for i in range(1, 40)) + "。"
    chunks = knowledge.chunk_text(text, size=120, overlap=30)
    assert len(chunks) > 2
    assert all(len(c) <= 120 for c in chunks), "片段不得超长"
    assert all(c.strip() for c in chunks)
    # 相邻片段必须有重叠：下一块的开头来自上一块的结尾
    assert chunks[1][:8] in chunks[0]


def test_chunk_text_empty_input():
    assert knowledge.chunk_text("") == []
    assert knowledge.chunk_text("   \n\n  ") == []


def test_expand_query_maps_colloquial_words():
    expanded = knowledge.expand_query("成都 带娃 少走路")
    assert "亲子" in expanded and "步行距离短" in expanded
    assert "成都 带娃 少走路" in expanded, "原话必须保留，只做追加"


def test_city_match_handles_suffix():
    assert knowledge._city_match("成都", "成都市")
    assert knowledge._city_match("成都市", "成都")
    assert not knowledge._city_match("成都", "杭州")
    assert not knowledge._city_match("", "成都")


# ---------------------------------------------------------------- 检索

def _row(source_id, city, title, vec):
    return SimpleNamespace(
        id=source_id, source_type="guide", source_id=source_id, city=city,
        title=title, content=f"{title}的正文", source_url=f"https://example.com/{source_id}",
        embedding=np.asarray(vec, dtype=np.float32).tobytes(),
    )


def test_search_ranks_thresholds_and_limits(monkeypatch):
    rows = [
        _row(1, "成都", "亲子", [1.0, 0.0]),
        _row(2, "成都", "交通", [0.0, 1.0]),
        _row(3, "成都", "模糊", [0.6, 0.8]),
    ]
    monkeypatch.setattr(knowledge, "_load_rows", lambda city: rows)
    monkeypatch.setattr(knowledge, "_embed_sync", lambda texts: [[1.0, 0.0]])

    out = knowledge.search("带娃", "成都", k=5, min_score=0.35)
    assert [r["source_id"] for r in out] == [1, 3], "按余弦降序，低于阈值丢弃"
    assert out[0]["score"] > out[1]["score"]
    assert set(out[0]) == {"source_type", "source_id", "city", "title", "content",
                           "source_url", "score"}

    assert len(knowledge.search("带娃", "成都", k=1)) == 1, "k 要生效"


def test_search_empty_library_skips_embedding(monkeypatch):
    monkeypatch.setattr(knowledge, "_load_rows", lambda city: [])
    called = []
    monkeypatch.setattr(knowledge, "_embed_sync", lambda texts: called.append(1) or [[1.0]])
    assert knowledge.search("成都", "成都") == []
    assert called == [], "空库不该为一次注定失败的检索付 embedding 费用"


def test_search_swallows_errors(monkeypatch):
    def boom(city):
        raise RuntimeError("no such table")

    monkeypatch.setattr(knowledge, "_load_rows", boom)
    assert knowledge.search("成都", "成都") == [], "检索异常必须降级为空，不能向上抛"


def test_search_skips_dimension_mismatch(monkeypatch):
    rows = [_row(1, "成都", "旧模型", [1.0, 0.0, 0.0])]
    monkeypatch.setattr(knowledge, "_load_rows", lambda city: rows)
    monkeypatch.setattr(knowledge, "_embed_sync", lambda texts: [[1.0, 0.0]])
    assert knowledge.search("成都", "成都") == [], "维度不一致的旧片段应跳过而不是报错"


# ---------------------------------------------------------------- 编排

def test_retrieve_node_degrades_to_empty(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("检索挂了")

    monkeypatch.setattr(knowledge, "search", boom)
    state = {"collected": {"destination": "成都", "preferences": "带娃"}, "logs": ["需求澄清"]}
    update = asyncio.run(retrieve_node(state))
    assert update["references"] == []
    assert update["logs"][-1] == "资料检索：本次未匹配到站内资料，按常规生成"


def test_retrieve_node_logs_hits(monkeypatch):
    refs = [{"title": "成都·亲子", "content": "人民公园适合带小孩", "source_id": 1,
             "source_type": "guide", "source_url": "https://example.com/1", "score": 0.8}]
    monkeypatch.setattr(knowledge, "search", lambda *a, **k: refs)
    state = {"collected": {"destination": "成都", "party": "2大1小"}, "logs": []}
    update = asyncio.run(retrieve_node(state))
    assert update["references"] == refs
    assert update["logs"][-1] == "资料检索：匹配 1 条站内资料"


FAKE_PLAN = {
    "budget_format": "items_and_additions_v1",
    "additional_costs": [],
    "title": "成都亲子一日",
    "summary": "看熊猫、逛公园",
    "days": [{
        "day": 1, "theme": "亲子", "reason": "按用户要求少走路",
        "items": [
            {"time": "09:00", "activity": "看熊猫", "poi": "成都大熊猫繁育研究基地", "type": "景点",
             "duration_min": 150, "transfer": "从酒店出发", "access": {"mode": "打车", "min": 20, "note": ""},
             "cost_estimate": 55, "cost_category": "门票", "note": "早点去"},
            {"time": "12:00", "activity": "午餐", "poi": "", "type": "餐饮", "duration_min": 60,
             "transfer": "步行", "access": {"mode": "步行", "min": 5, "note": ""},
             "cost_estimate": 120, "cost_category": "餐饮", "note": ""},
            {"time": "14:00", "activity": "人民公园喝茶", "poi": "人民公园", "type": "景点",
             "duration_min": 90, "transfer": "打车", "access": {"mode": "打车", "min": 15, "note": ""},
             "cost_estimate": 0, "cost_category": "其他", "note": "人少"},
            {"time": "17:00", "activity": "回酒店休息", "poi": "", "type": "休息", "duration_min": 60,
             "transfer": "步行", "access": {"mode": "步行", "min": 10, "note": ""},
             "cost_estimate": 0, "cost_category": "其他", "note": ""},
        ],
    }],
    "assumptions": [],
}


def _assert_reference_injection(monkeypatch, references, should_have):
    from app.services import itinerary as itinerary_module

    captured = []

    async def fake_chat(messages, temperature=0.5, timeout_ms=60000):
        captured.append(messages)
        return json.dumps(FAKE_PLAN, ensure_ascii=False)

    monkeypatch.setattr(itinerary_module, "chat", fake_chat)
    result = asyncio.run(
        itinerary_module.generate_itinerary({"destination": "成都", "days": 1}, references=references)
    )
    user_message = captured[0][1]["content"]
    assert user_message.startswith("【用户需求】")
    if should_have:
        assert "【可参考的站内资料】" in user_message
        assert "《成都·亲子》" in user_message
        assert "不要编造具体价格与营业时间" in user_message
    else:
        assert "【可参考的站内资料】" not in user_message
    assert result["days"], "注入资料不能破坏行程结构"


def test_generate_itinerary_injects_references(monkeypatch):
    refs = [{"title": "成都·亲子", "content": "人民公园适合带小孩，上午人少。", "source_id": 1,
             "source_type": "guide", "source_url": "https://example.com/1"}]
    _assert_reference_injection(monkeypatch, refs, True)


def test_generate_itinerary_without_references_is_unchanged(monkeypatch):
    _assert_reference_injection(monkeypatch, None, False)
    _assert_reference_injection(monkeypatch, [], False)


def test_reference_block_skips_contentless_items():
    from app.services.itinerary import _reference_block

    assert _reference_block([{"title": "空", "content": "  "}]) == ""
    block = _reference_block([{"title": "标题", "content": "正文"}] * 7)
    assert block.count("[1]") == 1 and "[5]" in block and "[6]" not in block, "最多 5 条"


def test_graph_contains_retrieve_before_generate():
    from app.services.graph import plan_graph

    nodes = set(plan_graph.get_graph().nodes)
    assert {"clarify", "retrieve", "generate", "verify", "weather"} <= nodes
    edges = {(e.source, e.target) for e in plan_graph.get_graph().edges}
    assert ("retrieve", "generate") in edges
    assert ("generate", "verify") in edges


def test_export_import_round_trip(tmp_path):
    """部署脚本的搬运格式：元信息 + base64 向量，导入前必须能完整校验。"""
    import base64

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import import_knowledge

    blob = base64.b64encode(b"\x00" * (knowledge.EMBED_DIM * 4)).decode("ascii")
    path = tmp_path / "knowledge.jsonl"
    path.write_text(
        json.dumps({"_meta": True, "version": 1, "dim": knowledge.EMBED_DIM, "count": 1}) + "\n"
        + json.dumps({
            "source_type": "guide", "source_id": 1, "chunk_index": 0, "city": "成都",
            "title": "成都·观光", "content": "正文", "tags": "", "embedding": blob,
            "source_url": "https://example.com/1",
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    meta, rows = import_knowledge.load(path)
    assert meta["count"] == 1 and len(rows[0]["embedding"]) == knowledge.EMBED_DIM * 4

    path.write_text(
        json.dumps({"_meta": True, "version": 1, "dim": knowledge.EMBED_DIM, "count": 2}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="条数不符"):
        import_knowledge.load(path)
