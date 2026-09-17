"""v0.15 目的地联想候选接口测试：/api/spots/regions。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.db.models import Spot  # noqa: E402


def _seed(db):
    """成都 3 条（青羊区 2、锦江区 1）+ 杭州 2 条（西湖区 2）+ 1 条脏数据。"""
    db.add_all([
        Spot(name="宽窄巷子", city="成都", district="青羊区", tags="历史街区",
             is_free=True, amap_id="C1", lng=104.0, lat=30.6),
        Spot(name="人民公园", city="成都", district="青羊区", tags="公园",
             is_free=True, amap_id="C2", lng=104.0, lat=30.6),
        Spot(name="春熙路", city="成都", district="锦江区", tags="商圈",
             is_free=True, amap_id="C3", lng=104.1, lat=30.6),
        Spot(name="西湖", city="杭州", district="西湖区", tags="湖泊",
             is_free=True, amap_id="H1", lng=120.1, lat=30.2),
        Spot(name="灵隐寺", city="杭州", district="西湖区", tags="寺庙",
             is_free=False, amap_id="H2", lng=120.1, lat=30.2),
        Spot(name="脏数据", city="", district=None, tags="x",
             is_free=True, amap_id="X1", lng=None, lat=None),
    ])
    db.commit()


def test_regions_grouping_and_order(client):
    c, Session = client
    db = Session()
    _seed(db)
    db.close()

    r = c.get("/api/spots/regions").json()
    cities = r["cities"]
    # 城市按景点数排序：成都 3 > 杭州 2；空 city 的脏数据被过滤
    assert [x["city"] for x in cities] == ["成都", "杭州"]
    assert cities[0]["count"] == 3 and cities[1]["count"] == 2
    # 区县按景点数排序：青羊区 2 > 锦江区 1
    assert [d["name"] for d in cities[0]["districts"]] == ["青羊区", "锦江区"]
    assert cities[0]["districts"][0]["count"] == 2


def test_regions_empty_db(client):
    c, _ = client
    r = c.get("/api/spots/regions").json()
    assert r == {"cities": []}
