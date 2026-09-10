"""v1.1 出行难度 / 交通可达性 / 在途耗时：契约、兜底与容错。

不调真模型（那部分在 test_m2 里），这里专测"形状永远不会缺"和"脏数据不崩"。
"""
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.db.models import AiPlan, User  # noqa: E402
from app.security import create_token  # noqa: E402
from app.services import itinerary as itinerary_module  # noqa: E402
from app.services.itinerary import DIFFICULTY_LEVELS, _ensure_traffic, _to_spot_days  # noqa: E402


def test_traffic_fields_filled_when_missing():
    """AI 一个交通字段都没给 → 兜底补齐，且数值能自洽（合计=步行+车程+候车）。"""
    it = _ensure_traffic(_to_spot_days({
        "title": "成都两日", "summary": "熊猫与火锅",
        "days": [
            {"day": 1, "theme": "熊猫", "items": [
                {"time": "09:00", "poi": "大熊猫基地", "transfer": "地铁3号线"}]},
            {"day": 2, "theme": "老城", "items": [
                {"time": "10:00", "poi": "宽窄巷子", "transfer": "步行"}]},
        ],
        "budget_summary": {"交通": 60, "总计": 300},
    }))
    for d in it["days"]:
        assert d["difficulty"]["level"] in DIFFICULTY_LEVELS
        assert d["difficulty"]["basis"], "难度必须给判断依据"
        assert isinstance(d["transit"]["modes"], list) and d["transit"]["modes"]
        assert d["transit"]["combo"]
        t = d["timing"]
        assert t["total_min"] == t["walk_min"] + t["ride_min"] + t["wait_min"]
        assert t["peak_buffer_min"] > 0, "要给高峰波动"
        for s in d["spots"]:
            assert "access" in s and s["access"]["mode"], "每个点都要有接驳说明"
    assert it["traffic"]["cost"] == 60, "交通花费缺省取预算里的交通项"
    assert it["traffic"]["difficulty_overall"] in DIFFICULTY_LEVELS
    assert it["traffic"]["total_min"] == sum(d["timing"]["total_min"] for d in it["days"])


def test_difficulty_default_by_walk_and_transfers():
    """没给难度时按步行量与换乘次数推算：走得多换乘多 = 较费体力。"""
    hard = _ensure_traffic({"days": [{"day": 1, "items": [], "timing": {"walk_min": 180, "transfers": 4}}]})
    easy = _ensure_traffic({"days": [{"day": 1, "items": [], "timing": {"walk_min": 30, "transfers": 0}}]})
    assert hard["days"][0]["difficulty"]["level"] == "较费体力"
    assert easy["days"][0]["difficulty"]["level"] == "轻松"


def test_malformed_items_do_not_crash():
    """模型偶尔在 items 里塞嵌套数组/字符串 → 跳过脏元素，不让整次规划崩掉。"""
    it = _ensure_traffic(_to_spot_days({
        "days": [{"day": 1, "items": [
            {"time": "09:00", "poi": "西湖"},
            ["乱入的数组", {"poi": "嵌套"}],
            "乱入的字符串",
            {"time": "12:00", "poi": "灵隐寺"},
        ]}]
    }))
    names = [s["name"] for s in it["days"][0]["spots"]]
    assert names == ["西湖", "灵隐寺"], "脏元素应被跳过，正常景点照常保留"


def test_timing_capped_to_sane_range():
    """模型给出"步行300分钟"这类离谱值时封顶，且合计始终等于三项之和。"""
    it = _ensure_traffic({"days": [{"day": 1, "items": [], "timing": {
        "walk_min": 9999, "ride_min": 500, "wait_min": 300, "transfers": 99, "peak_buffer_min": 500}}]})
    t = it["days"][0]["timing"]
    assert t["walk_min"] == 240 and t["ride_min"] == 240 and t["wait_min"] == 90
    assert t["transfers"] == 8 and t["peak_buffer_min"] == 60
    assert t["total_min"] == 240 + 240 + 90
    assert it["traffic"]["total_min"] == t["total_min"], "全程耗时必须等于各天之和"


def test_strip_prompt_labels():
    """模型照抄提示词留下的"就近站点："这类前缀要去掉，展示才干净。"""
    it = _ensure_traffic({"days": [{"day": 1, "items": [], "transit": {
        "modes": ["地铁"], "combo": "推荐组合：地铁2号线→步行",
        "stations": ["就近站点：永宁门站"], "parking": "停车条件：车位紧张"}}]})
    tr = it["days"][0]["transit"]
    assert tr["combo"] == "地铁2号线→步行"
    assert tr["stations"] == ["永宁门站"]
    assert tr["parking"] == "车位紧张"


def test_history_list_shows_traffic_for_comparison(client):
    """历史列表带上难度/在途/交通费，才能横向比较不同目的地。"""
    c, Session = client
    db = Session()
    u = User(username=f"traf_{uuid.uuid4().hex[:6]}", password_hash="x", nickname="比价")
    db.add(u)
    db.commit()
    db.add(AiPlan(
        user_id=u.id, session_id="s", status="completed",
        intent_json='{"destination":"西安","days":2}',
        process_json="[]",
        result_json=json.dumps({
            "title": "西安两日",
            "days": [{"day": 1, "spots": [{"name": "兵马俑"}]}],
            "traffic": {"total_min": 155, "cost": 88, "difficulty_overall": "较费体力"},
        }, ensure_ascii=False),
    ))
    db.commit()
    token = create_token(u.id)
    db.close()

    row = c.get("/api/plan/history", headers={"Authorization": f"Bearer {token}"}).json()["items"][0]
    assert row["difficulty"] == "较费体力"
    assert row["travel_min"] == 155
    assert row["traffic_cost"] == 88
