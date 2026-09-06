"""M1 需求澄清模块测试（pytest 版，断言与 Node 版 test/m1.test.js 等价）。

验收标准（对应规划文档）：
  1. 故意只说"去成都玩"（缺天数）→ AI 必须追问而不是硬编
  2. 补齐必需信息后 → done=True，collected 正确累积
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.services.clarify import clarify_turn  # noqa: E402


def test_missing_days_should_ask():
    """场景1：信息不全应追问（对应 Node 场景1）"""
    r1 = asyncio.run(clarify_turn([], "我想去成都玩"))
    assert r1["done"] is False, "缺天数时 done 应为 False"
    assert r1["question"], "应有追问文案"
    assert "成都" in str(r1["collected"].get("destination", "")), "目的地应被识别为成都"


def test_complete_info_should_pass():
    """场景2：补齐后应放行（对应 Node 场景2）"""
    r2 = asyncio.run(
        clarify_turn(
            [
                {"role": "user", "content": "我想去成都玩"},
                {"role": "assistant", "content": "请问玩几天？"},
            ],
            "10月1日到3日共3天，2大1小，预算3000，带娃要悠闲，爱熊猫",
            {"destination": "成都"},
        )
    )
    assert r2["done"] is True, "信息齐全时 done 应为 True"
    assert r2["collected"].get("days") == 3, "天数应为 3"
    assert "1小" in str(r2["collected"].get("party", "")), "人数构成应含 1小"
