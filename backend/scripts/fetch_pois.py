"""高德 POI 采集脚本（v0.5 数据底座）：首批 5 城真实景点入库。

用法（在 backend/ 目录下运行）：
  试跑单城：  python scripts/fetch_pois.py --city 成都 --max-per-city 30
  全量 5 城：  python scripts/fetch_pois.py --all

说明：
- 数据来源：高德地图开放平台 v5 搜索 API（官方接口，非爬虫，稳定合规）
- 免费判断：POI 接口不返回票价，按名称/类型粗判（公园/街区/广场/湖泊=免费），后台管理员可改
- 限流：个人开发者 key QPS=3，每次请求间隔 0.4 秒
"""
import argparse
import json
import sys
import time
from pathlib import Path

import httpx

# 保证 scripts/ 下能 import app 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.db.database import SessionLocal, init_db  # noqa: E402
from app.db.models import Spot  # noqa: E402

API_URL = "https://restapi.amap.com/v5/place/text"
CITIES = ["成都", "杭州", "西安", "北京", "三亚"]
# 每城用多组关键词抓取，覆盖不同类型，合并去重
KEYWORDS = ["风景区", "公园", "博物馆", "寺庙", "历史古迹", "古镇"]
FREE_HINTS = ("公园", "街区", "广场", "湖", "古镇", "步行街", "绿地")
REQUEST_INTERVAL = 0.4


def is_free_guess(name: str, type_: str) -> bool:
    """免费粗判：名称或类型命中休闲类关键词 → 免费。后台管理员可随时改。"""
    text = f"{name}{type_}"
    return any(hint in text for hint in FREE_HINTS)


def parse_poi(item: dict) -> dict:
    """把高德 v5 POI 条目映射为 spots 表字段（纯函数，便于测试）。"""
    location = item.get("location", "")
    lng, lat = (None, None)
    if "," in location:
        try:
            lng, lat = (float(x) for x in location.split(","))
        except ValueError:
            pass
    photos = item.get("photos") or []
    image_url = photos[0].get("url", "") if photos else ""
    name = item.get("name", "")
    type_ = item.get("type", "")
    return {
        "name": name,
        # 城市口径归一化："成都市"→"成都"，与原型筛选下拉一致
        "city": (item.get("cityname", "") or item.get("province", "")).rstrip("市"),
        "district": item.get("adname", ""),
        "address": item.get("address", ""),
        # 类型串如"风景名胜;风景名胜;国家级景区"→取分号分隔的末两级做标签
        "tags": ",".join(filter(None, type_.split(";")[-2:])) if type_ else "",
        "description": "",
        "is_free": is_free_guess(name, type_),
        "price": None,
        "lng": lng,
        "lat": lat,
        "source": "高德地图开放平台",
        "source_url": f"https://www.amap.com/detail/{item.get('id', '')}",
        "image_url": image_url,
        "amap_id": item.get("id", ""),
    }


def fetch_city(client: httpx.Client, city: str, keyword: str, max_pages: int = 4) -> list[dict]:
    """抓取某城某关键词的 POI 列表（每页 25 条，最多 max_pages 页）。"""
    out = []
    for page in range(1, max_pages + 1):
        resp = client.get(
            API_URL,
            params={
                "key": settings.amap_api_key,
                "keywords": keyword,
                "region": city,
                "city_limit": "true",
                "page_size": 25,
                "page_num": page,
                "show_fields": "photos",
            },
            timeout=15,
        )
        data = resp.json()
        if data.get("status") != "1":
            print(f"  [WARN] {city}/{keyword} 第{page}页接口异常: {data.get('info')}")
            break
        pois = data.get("pois", [])
        out.extend(pois)
        # 返回不足一页说明已到底
        if len(pois) < 25:
            break
        time.sleep(REQUEST_INTERVAL)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", help="只采集指定城市，如：成都")
    parser.add_argument("--all", action="store_true", help="采集全部 5 城")
    parser.add_argument("--max-per-city", type=int, default=100, help="每城入库上限")
    args = parser.parse_args()

    if not args.all and not args.city:
        parser.error("请指定 --city 城市名 或 --all 全量采集")
    cities = CITIES if args.all else [args.city]

    init_db()
    session = SessionLocal()
    existing = {row[0] for row in session.query(Spot.amap_id).all()}
    total_new = 0

    with httpx.Client() as client:
        for city in cities:
            raw = []
            for kw in KEYWORDS:
                raw.extend(fetch_city(client, city, kw))
                time.sleep(REQUEST_INTERVAL)
            city_new = 0
            for item in raw:
                if city_new >= args.max_per_city:
                    break
                poi = parse_poi(item)
                if not poi["name"] or not poi["amap_id"] or poi["amap_id"] in existing:
                    continue
                if poi["city"] and city not in poi["city"]:
                    continue  # region 漏网的外城数据
                session.add(Spot(**poi))
                existing.add(poi["amap_id"])
                city_new += 1
            session.commit()
            total_new += city_new
            print(f"[{city}] 新入库 {city_new} 条")

    count = session.query(Spot).count()
    session.close()
    print(f"完成：本次新入库 {total_new} 条，spots 表总计 {count} 条")


if __name__ == "__main__":
    main()
