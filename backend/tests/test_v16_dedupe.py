"""v1.6 回归：同一天里重复出现的景点必须被合并。

背景（实测报障）：北京 2 天行程第 2 天出现"14:00 颐和园、17:00 颐和园"，
外加"八达岭长城 → 回市区吃饭 → 颐和园"这种一天跑 60 公里的排法。
同一景点在同一天重复出现基本都是模型笔误，不去重就会原样端给用户。

这里只测去重这一段纯逻辑，不联网、不调模型：
    verify_itinerary_spots(..., amap_enabled=False) 走本地 spots 库匹配。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.db.models import Spot
from app.services import map_verify


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add_all([
        Spot(name="颐和园", city="北京", district="海淀区", address="新建宫门路19号",
             tags="风景名胜", lng=116.27, lat=39.99, amap_id="B0FFH1"),
        Spot(name="八达岭长城", city="北京", district="延庆区", address="G6京藏高速58号",
             tags="风景名胜", lng=116.02, lat=40.36, amap_id="B0FFH2"),
    ])
    db.commit()
    return db


def test_same_day_duplicate_spot_merged():
    """同一天两次"颐和园" → 只保留第一次，并留一条去重日志。"""
    db = _db()
    try:
        itin = {"days": [{
            "day": 2, "theme": "文化之旅",
            "spots": [
                {"name": "八达岭长城", "time": "09:00"},
                {"name": "颐和园", "time": "14:00"},
                {"name": "颐和园", "time": "17:00"},
            ],
        }]}
        new_itin, logs = map_verify.verify_itinerary_spots(itin, "北京", db, amap_enabled=False)
        names = [s["name"] for s in new_itin["days"][0]["spots"]]
        assert names == ["八达岭长城", "颐和园"], names
        dedupe_logs = [l for l in logs if l["action"] == "dedupe"]
        assert len(dedupe_logs) == 1
        assert "3 → 2" in dedupe_logs[0]["detail"]
    finally:
        db.close()


def test_cross_day_same_spot_kept():
    """跨天重复不算错（不同天各去一次是合理的），不能被合并掉。"""
    db = _db()
    try:
        itin = {"days": [
            {"day": 1, "theme": "A", "spots": [{"name": "颐和园"}]},
            {"day": 2, "theme": "B", "spots": [{"name": "颐和园"}]},
        ]}
        new_itin, logs = map_verify.verify_itinerary_spots(itin, "北京", db, amap_enabled=False)
        assert [len(d["spots"]) for d in new_itin["days"]] == [1, 1]
        assert not [l for l in logs if l["action"] == "dedupe"]
    finally:
        db.close()


def test_alias_names_hitting_same_poi_merged():
    """名字不同、但都命中同一个真实 POI 时，也要合并。

    这是线上真实出现过的那种重复：模型写了两个说法，_db_lookup 用 like 匹配，
    两次都落到"颐和园"这一条记录上，页面就出现两个同名同坐标的地点。
    """
    db = _db()
    try:
        itin = {"days": [{"day": 2, "theme": "文化", "spots": [
            {"name": "颐和园"}, {"name": "和园"},
        ]}]}
        new_itin, logs = map_verify.verify_itinerary_spots(itin, "北京", db, amap_enabled=False)
        assert [s["name"] for s in new_itin["days"][0]["spots"]] == ["颐和园"]
        assert any(l["action"] == "dedupe" for l in logs)
    finally:
        db.close()
