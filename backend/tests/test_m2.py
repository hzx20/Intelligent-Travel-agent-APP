"""M2 行程生成模块测试（pytest 版，断言与 Node 版 test/m2.test.js 等价）。

验收标准（对应规划文档）：多组标准需求，行程天数与要求一致、每日安排非空、预算汇总存在
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.services.itinerary import generate_itinerary  # noqa: E402

CASES = [
    (
        "成都 3 天亲子悠闲",
        {"destination": "成都", "days": 3, "dates": "10月1日-3日", "party": "2大1小", "budget": 3000, "pace": "悠闲", "preferences": "带5岁小孩"},
    ),
    (
        "杭州 2 天周末",
        {"destination": "杭州", "days": 2, "party": "2人", "budget": 1500, "pace": "中等"},
    ),
    (
        "北京 1 天紧凑",
        {"destination": "北京", "days": 1, "party": "1人", "pace": "紧凑"},
    ),
]


@pytest.mark.parametrize("name,req", CASES, ids=[c[0] for c in CASES])
def test_generate_itinerary(name, req):
    it = asyncio.run(generate_itinerary(req))
    assert it.get("title"), "应有标题"
    assert isinstance(it.get("days"), list) and len(it["days"]) == req["days"], (
        f"天数应为 {req['days']}，实际 {len(it.get('days') or [])}"
    )
    for i, d in enumerate(it["days"]):
        assert isinstance(d.get("items"), list) and len(d["items"]) >= 3, f"Day{i + 1} 安排应不少于 3 条"
        for item in d["items"]:
            assert item.get("poi"), f"Day{i + 1} {item.get('time')} 应有 poi 字段"
            assert item.get("time"), f"Day{i + 1} 应有时间"
        # 形状契约（v1.0 验收教训）：下游核实/前端只认 spots/name，缺了就是空行程
        assert d.get("spots"), f"Day{i + 1} 应有 spots（items/poi 自动转换）"
        assert all(s.get("name") for s in d["spots"]), f"Day{i + 1} spots 每项应有 name"
    assert it.get("budget_summary") and "总计" in it["budget_summary"], "应有预算汇总"
    # v1.1 解说契约：推荐理由 / 方案对比 / 决策依据（界面上"为什么这么排"全靠这三项）
    assert any((d.get("reason") or "").strip() for d in it["days"]), "至少一天要有推荐理由"
    alts = it.get("alternatives") or []
    assert len(alts) >= 1 and all((a.get("option") or "").strip() for a in alts), "应给出方案对比"
    assert any(a.get("chosen") for a in alts), "方案对比要标明最终选了哪个"
    assert len(it.get("decision_basis") or []) >= 1, "应给出决策依据"
