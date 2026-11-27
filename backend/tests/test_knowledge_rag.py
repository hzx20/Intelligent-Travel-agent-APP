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
        # 约束是「别照抄资料里的价」，不是「别给价格」：后者会让整份行程的
        # 预算总计变成 0（2026-11-27 线上实测），所以两者都要在提示里说清楚。
        assert "不要照抄成确定报价" in user_message
        assert "cost_estimate" in user_message
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


# ---------------------------------------------------------------- 引用落地

def test_strip_stale_facts_removes_prices_and_dates():
    text = "门票 60 元，人均 50 元左右。更新日期：2019-05-01"
    cleaned = knowledge.strip_stale_facts(text)
    assert "60" not in cleaned and "50" not in cleaned
    assert "2019" not in cleaned
    assert "门票" in cleaned and "人均" in cleaned


def test_strip_stale_facts_keeps_normal_sentences():
    text = "地铁 1 号线直达，车程 20 分钟，适合带小孩。"
    assert knowledge.strip_stale_facts(text) == text


def test_stale_fact_hits_counts_markers():
    assert knowledge.stale_fact_hits("票价￥120，更新日期 2020-01-01") >= 2
    assert knowledge.stale_fact_hits("步行十分钟到湖边") == 0


def test_match_refs_prefers_exact_spot_title():
    from app.services import citations

    refs = [
        {"title": "观光", "content": "宽窄巷子下午人多，人民公园可以喝茶。"},
        {"title": "成都动物园", "content": "适合带小孩，上午人少。"},
    ]
    assert citations.match_refs("成都动物园", refs) == [2]
    assert citations.match_refs("宽窄巷子", refs) == [1]


def test_match_refs_does_not_match_generic_words():
    from app.services import citations

    # 「人民」只是通用前缀：正文里出现「人民路」不代表就是人民公园
    refs = [{"title": "交通", "content": "人民路一带小吃很多，公交可到。"}]
    assert citations.match_refs("人民公园", refs) == []
    assert citations.match_refs("公园", refs) == []


def test_attach_citations_is_idempotent_and_per_day():
    from app.services import citations

    refs = [{"title": "观光", "content": "宽窄巷子适合下午去。"}]
    itinerary = {
        "days": [
            {"day": 1, "spots": [{"name": "宽窄巷子"}, {"name": "春熙路"}]},
            {"day": 2, "spots": [{"name": "青城山"}]},
        ]
    }
    first = citations.attach_citations(itinerary, refs)
    second = citations.attach_citations(itinerary, refs)
    assert first == second
    assert itinerary["days"][0]["spots"][0]["ref_ids"] == [1]
    assert "ref_ids" not in itinerary["days"][0]["spots"][1]
    assert itinerary["days"][0]["ref_ids"] == [1]
    assert "ref_ids" not in itinerary["days"][1]
    assert first["spots_with_refs"] == 1 and first["days_with_refs"] == 1


def test_attach_citations_without_refs_keeps_structure():
    from app.services import citations

    itinerary = {"days": [{"day": 1, "spots": [{"name": "宽窄巷子"}]}]}
    stats = citations.attach_citations(itinerary, [])
    assert stats["refs"] == 0
    assert "ref_ids" not in itinerary["days"][0]["spots"][0]


def test_plan_density_ignores_meals_and_rest():
    from app.services.itinerary import plan_density

    itinerary = {
        "days": [
            {"day": 1, "spots": [
                {"name": "宽窄巷子"}, {"name": "人民公园"},
                {"name": "午餐", "timeBlock": "餐饮"}, {"name": "休息", "type": "休息"},
            ]},
            {"day": 2, "spots": [{"name": "青城山"}]},
        ]
    }
    density = plan_density(itinerary, {"pace": "悠闲"})
    assert density["counts"] == [2, 1]
    # 悠闲档的下限也是 3：之前给 2，模型排 2 个就判达标，提示词里
    # 「每天至少 3 个具名地点」等于白写。悠闲是「每处待久一点」，不是「少去一处」。
    assert density["thin_days"] == [1, 2]
    assert density["floor"] == 3


def test_public_quotas_cover_plan_endpoints():
    from app.core.shared_limits import PUBLIC_QUOTAS

    for path in ("/api/plan/stream", "/api/plan/jobs", "/api/plan/jobs/create"):
        assert path in PUBLIC_QUOTAS, f"{path} 没有限流：游客可无限提交长文本生成"
        limit, window = PUBLIC_QUOTAS[path]
        assert 0 < limit <= 10 and window == 60


def test_reference_block_strips_prices():
    from app.services.itinerary import _reference_block

    block = _reference_block([
        {"title": "成都·美食", "content": "玉林路小馆人均 60 元左右，适合家庭。"}
    ])
    assert "60" not in block
    assert "玉林路" in block


def test_display_references_keeps_original_numbers():
    """行程里标 [1]..[5]，底部合并同名章节后编号还得对得上。"""
    from app.services.graph import _display_references

    refs = [
        {"source_id": 2557, "source_type": "guide", "title": "成都·观光", "source_url": "u1"},
        {"source_id": 2557, "source_type": "guide", "title": "成都·观光", "source_url": "u1"},
        {"source_id": 17, "source_type": "spot", "title": "成都动物园", "source_url": "u2"},
        {"source_id": 55, "source_type": "spot", "title": "塔子山公园", "source_url": "u3"},
        {"source_id": 2557, "source_type": "guide", "title": "成都·观光", "source_url": "u1"},
    ]
    display = _display_references(refs)
    assert [d["title"] for d in display] == ["成都·观光", "成都动物园", "塔子山公园"]
    assert display[0]["ref_ids"] == [1, 2, 5]
    assert display[1]["ref_ids"] == [3]
    assert display[2]["ref_ids"] == [4]
    assert "content" not in display[0]


def test_display_references_empty():
    from app.services.graph import _display_references

    assert _display_references(None) == []
    assert _display_references([]) == []


def test_shared_peer_skips_proxy_addresses():
    """反代没传真实 IP 时，按 127.0.0.1 限流等于全站共享额度。"""
    from app.core.shared_limits import _shared_peer

    # 172/10 段才是一整片内网共用一个 key；回环是本机单来源，照常计数
    assert _shared_peer("172.31.240.2")
    assert _shared_peer("10.0.0.5")
    assert not _shared_peer("127.0.0.1")
    assert not _shared_peer("134.175.137.18")
    # TestClient 用 "testclient" 当 host：解析不出 IP，当作单个来源
    assert not _shared_peer("testclient")


def test_normalize_cost_category_maps_model_variants():
    """模型写「景点门票」「市内交通」「购物」不能被整份打回。"""
    from app.services.itinerary import _normalize_cost_category as norm

    assert norm("门票") == "门票"
    assert norm("景点门票") == "门票"
    assert norm("市内门票") == "门票"
    assert norm("市内交通") == "交通"
    assert norm("餐费") == "餐饮"
    assert norm("购物") == "其他"
    assert norm("") is None and norm(None) is None


def test_fill_thin_days_adds_one_and_dedupes():
    from app.services.itinerary import fill_thin_days, has_thin_day

    itinerary = {
        "days": [
            {"day": 1, "spots": [{"name": "宽窄巷子"}]},
            {"day": 2, "spots": [{"name": "A"}, {"name": "B"}, {"name": "C"}]},
        ]
    }
    assert has_thin_day(itinerary, 3), "第 1 天只有 1 个点"
    candidates = [
        {"name": "宽窄巷子"},      # 已经排过，必须跳过
        {"name": "人民公园"},
        {"name": "塔子山公园"},
    ]
    filled = fill_thin_days(itinerary, candidates, floor=3, per_day=1)
    assert filled == 1, "per_day=1 时每天最多补 1 个"
    day1 = itinerary["days"][0]["spots"]
    assert day1[-1]["name"] == "人民公园"
    assert day1[-1]["filled"] is True
    assert len(itinerary["days"][1]["spots"]) == 3, "达标的天不动"
    assert not has_thin_day(itinerary, 3) or len(itinerary["days"][0]["spots"]) < 3


def test_fill_thin_days_without_candidates_is_noop():
    from app.services.itinerary import fill_thin_days

    itinerary = {"days": [{"day": 1, "spots": [{"name": "宽窄巷子"}]}]}
    assert fill_thin_days(itinerary, [], floor=3) == 0
    assert len(itinerary["days"][0]["spots"]) == 1


def test_city_spots_puts_county_level_cities_last():
    """补位优先市区：把都江堰补进「动物园+塔子山」那天，路线要横穿整个平原。"""
    from app.services.knowledge import city_spots

    rows = city_spots("成都", 40)
    if not rows:
        pytest.skip("本地没有 spots 数据")
    names = [r["name"] for r in rows]
    assert names, "成都要有候选"
    if "都江堰景区" in names and "人民公园" in names:
        assert names.index("人民公园") < names.index("都江堰景区")


def test_fill_thin_days_reaches_three_by_default():
    """模型一天只排 1 个点时，默认补 2 个凑到 3 个，而不是停在 2 个。"""
    from app.services.itinerary import fill_thin_days

    itinerary = {"days": [{"day": 1, "spots": [{"name": "A"}]}]}
    filled = fill_thin_days(itinerary, [{"name": "B"}, {"name": "C"}, {"name": "D"}], floor=3)
    assert filled == 2
    assert [s["name"] for s in itinerary["days"][0]["spots"]] == ["A", "B", "C"]


def test_fill_thin_days_reaches_three_by_default():
    """模型一天只排 1 个点时，默认补 2 个凑到 3 个，而不是停在 2 个。"""
    from app.services.itinerary import fill_thin_days

    itinerary = {"days": [{"day": 1, "spots": [{"name": "A"}]}]}
    filled = fill_thin_days(itinerary, [{"name": "B"}, {"name": "C"}, {"name": "D"}], floor=3)
    assert filled == 2
    assert [s["name"] for s in itinerary["days"][0]["spots"]] == ["A", "B", "C"]


# ---------------------------------------------------------------- 补点时刻

def test_slot_after_continues_from_last_spot():
    """补的点要接在当天最后一个点后面，不能凭空给 09:00。"""
    from app.services.itinerary import _slot_after

    spots = [
        {"name": "A", "time": "09:00", "duration_min": 120},
        {"name": "B", "time": "13:30", "duration_min": 90},
    ]
    # 13:30 + 90 分钟 = 15:00，再加 30 分钟路上时间
    assert _slot_after(spots) == "15:30"


def test_slot_after_starts_at_nine_without_times():
    from app.services.itinerary import _slot_after

    assert _slot_after([{"name": "A"}]) == "09:00"
    assert _slot_after([]) == "09:00"


def test_slot_after_gives_up_when_day_is_full():
    """排到 20:00 之后就不补了：塞一个 21:30 的公园没有意义。"""
    from app.services.itinerary import _slot_after

    spots = [{"name": "A", "time": "18:00", "duration_min": 180}]
    assert _slot_after(spots) == ""


def test_fill_thin_days_sets_time_on_filled_spots():
    from app.services.itinerary import fill_thin_days

    itinerary = {
        "days": [
            {"day": 1, "spots": [{"name": "A", "time": "09:00", "duration_min": 120}]},
        ]
    }
    filled = fill_thin_days(itinerary, [{"name": "B"}, {"name": "C"}], floor=3)
    assert filled == 2
    spots = itinerary["days"][0]["spots"]
    assert [s["time"] for s in spots] == ["09:00", "11:30", "14:00"]
    assert all(s.get("duration_min") == 120 for s in spots[1:])


def test_fill_thin_days_skips_day_that_is_already_full():
    from app.services.itinerary import fill_thin_days

    itinerary = {
        "days": [
            {"day": 1, "spots": [{"name": "A", "time": "19:00", "duration_min": 120}]},
        ]
    }
    assert fill_thin_days(itinerary, [{"name": "B"}, {"name": "C"}], floor=3) == 0
    assert len(itinerary["days"][0]["spots"]) == 1
