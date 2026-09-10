"""v1.1 住宿推荐：五维综合评分、优先推荐纠偏、兜底与历史列表字段。

不调真模型；归一化与接口层全走内存库/纯函数。
"""
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.db.models import AiPlan, User  # noqa: E402
from app.security import create_token  # noqa: E402
from app.services.itinerary import _ensure_stays  # noqa: E402

THREE_STAYS = {
    "city": "成都",
    "stays": [
        {"name": "经济连锁", "type": "经济连锁", "price_min": 150, "price_max": 220,
         "scores": {"value": 9, "transit": 5, "dining": 7, "safety": 7, "comfort": 6},
         "pros": ["便宜"], "cons": ["隔音一般"], "reason": "预算优先", "fit": "学生党", "stage": "全程"},
        {"name": "中端商务", "type": "中端酒店", "price_min": 320, "price_max": 480,
         "scores": {"value": 7, "transit": 9, "dining": 8, "safety": 9, "comfort": 8},
         "reason": "地铁上盖", "fit": "商务/家庭", "stage": "全程"},
        {"name": "江景民宿", "type": "特色民宿", "price_min": 600, "price_max": 900,
         "scores": {"value": 5, "transit": 4, "dining": 6, "safety": 8, "comfort": 9}},
    ],
    "stay_pick": {"name": "不存在的名字"},
}


def test_scores_weighted_and_pick_corrected():
    """综合分=加权（代码算，不信模型）；stay_pick 指向不存在的名字时纠正为最高分者。"""
    it = _ensure_stays(THREE_STAYS)
    stays = it["stays"]
    assert [s["name"] for s in stays] == ["中端商务", "经济连锁", "江景民宿"], "应按综合分降序"
    assert stays[0]["recommended"] is True and stays[1]["recommended"] is False
    # 手工核对：中端 = 7*.25+9*.25+8*.15+9*.2+8*.15 = 8.2
    assert stays[0]["score_total"] == 8.2
    assert it["stay_pick"]["name"] == "中端商务", "模型给的 pick 名字不存在 → 以综合分最高者为准"


def test_price_range_swapped_and_scores_clamped():
    """低价>高价自动交换；评分越界（0/100）夹到 1-10。"""
    it = _ensure_stays({"stays": [
        {"name": "价格写反的", "price_min": 800, "price_max": 300,
         "scores": {"value": 0, "transit": 100, "dining": 5, "safety": 5, "comfort": 5}},
    ]})
    s = it["stays"][0]
    assert (s["price_min"], s["price_max"]) == (300, 800), "价格区间应自动扶正"
    assert s["scores"]["value"] == 1 and s["scores"]["transit"] == 10, "评分必须夹在 1-10"
    assert s["price_range"] == "300-800 元/晚"


def test_missing_stays_gets_neutral_fallback():
    """一个住宿都没给 → 兜底条目（不编造酒店名），理由讲选址思路。"""
    it = _ensure_stays({"city": "杭州"})
    s = it["stays"][0]
    assert "杭州" in s["name"] and "待选" in s["name"], "兜底名不应编造具体酒店"
    assert s["price_range"] == "价格待询"
    assert it["stay_pick"]["name"] == s["name"]


def test_malformed_stay_entries_skipped(client):
    """非字典元素与无名字条目跳过，不崩。"""
    it = _ensure_stays({"stays": ["乱入字符串", {"area": "没有名字"}, {"name": "正常酒店"}]})
    assert [s["name"] for s in it["stays"]] == ["正常酒店"]


def test_pick_wins_over_score_when_valid():
    """模型点名有效候选（哪怕是分数第二的）→ ★ 徽章跟随最终建议，两者永不分家。

    为什么允许不选分最高的：模型了解用户语境（如带娃+预算有限，中端比高档更合适），
    分数只是五维通用评估，选档权交给模型，但展示必须一致。
    """
    it = _ensure_stays({**THREE_STAYS, "stay_pick": {"name": "经济连锁", "why": "预算优先"}})
    by_name = {s["name"]: s for s in it["stays"]}
    assert by_name["经济连锁"]["recommended"] is True
    assert by_name["中端商务"]["recommended"] is False, "分数再高也不能抢徽章"
    assert sum(1 for s in it["stays"] if s["recommended"]) == 1, "有且只有一个优先推荐"
    assert it["stay_pick"]["name"] == "经济连锁" and it["stay_pick"]["why"] == "预算优先"


def test_history_list_shows_stay_range(client):
    """历史列表带优先推荐住宿的价格区间。"""
    c, Session = client
    db = Session()
    u = User(username=f"stay_{uuid.uuid4().hex[:6]}", password_hash="x", nickname="住客")
    db.add(u)
    db.commit()
    result = json.dumps({
        "title": "成都两日",
        "days": [{"day": 1, "spots": [{"name": "宽窄巷子"}]}],
        "stays": [{"name": "中端商务", "price_range": "320-480 元/晚", "recommended": True}],
        "stay_pick": {"name": "中端商务"},
    }, ensure_ascii=False)
    db.add(AiPlan(user_id=u.id, session_id="s", status="completed",
                  intent_json='{"destination":"成都","days":2}',
                  process_json="[]", result_json=result))
    db.commit()
    token = create_token(u.id)
    db.close()

    row = c.get("/api/plan/history", headers={"Authorization": f"Bearer {token}"}).json()["items"][0]
    assert row["stay_range"] == "320-480 元/晚"
