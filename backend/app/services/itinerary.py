"""M2 行程生成模块（LangChain 版）。

对应关系：server/src/itinerary.js → backend/app/services/itinerary.py
- SYSTEM_PROMPT 原文照搬，保证 AI 行为一致
- 天数不符自动重排一次的自检纠偏逻辑保持不变
- 输出：{ title, summary, days[], budget_summary, assumptions }
  POI 名称为 AI 建议的真实知名地点，M3 模块会用地图 API 逐一核实
"""
import json

from app.core.llm import chat, extract_json

SYSTEM_PROMPT = """你是资深旅行行程设计师。根据用户需求生成行程，输出纯 JSON（不要任何多余文字）：
{
  "title": "行程标题（10字内）",
  "summary": "一句话总览",
  "days": [
    {
      "day": 1,
      "theme": "当天主题（6字内）",
      "reason": "当天这样安排的理由（1-2句：为什么选这些点、怎么贴合用户偏好）",
      "difficulty": {
        "level": "轻松|适中|较费体力（三选一）",
        "basis": ["判断依据1：如 全天步行约6公里", "依据2：如 灵隐寺需爬台阶、山路湿滑"]
      },
      "transit": {
        "modes": ["地铁", "步行"],
        "combo": "推荐组合：如 地铁1号线龙翔桥站→步行800米→景区电瓶车",
        "stations": ["就近站点：如 龙翔桥站(地铁1号线)"],
        "parking": "停车条件：如 景区停车场位少，建议地铁（自驾请写清楚）"
      },
      "timing": {
        "walk_min": 数字（全天步行总时长，分钟；按 5 公里/小时折算，通常 30-120）,
        "ride_min": 数字（全天车程总时长，分钟；通常 20-120）,
        "wait_min": 数字（候车+换乘等待合计，分钟；单次 5-15，全天通常 15-45）,
        "transfers": 数字（换乘次数，通常 0-4）,
        "peak_buffer_min": 数字（高峰时段额外耗时，分钟；通常 15-40）
      },
      "items": [
        {
          "time": "09:00",
          "activity": "做什么（一句话）",
          "poi": "具体地点名（真实存在的知名地点，供后续地图核实）",
          "type": "景点/餐饮/交通/酒店/自由活动",
          "transfer": "从上一地点怎么去（文字，第一站写 从酒店/车站出发）",
          "access": {"mode": "步行|地铁|公交|打车|自驾", "min": 数字（去下一站耗时，分钟）, "note": "接驳说明，如 出站步行600米"},
          "cost_estimate": 数字（本项预计花费，单位元，按整个出行队伍算）,
          "note": "一句话小贴士"
        }
      ]
    }
  ],
  "stays": [
    {
      "name": "住宿名称或商圈+档次（如 春熙路商圈中端酒店）",
      "area": "所在区域（如 锦江区）",
      "type": "经济连锁|中端酒店|高端酒店|特色民宿",
      "price_min": 数字（淡季每晚低价，元）,
      "price_max": 数字（旺季每晚高价，元）,
      "scores": {"value": 1-10（性价比）, "transit": 1-10（交通便利）, "dining": 1-10（餐饮生活配套）, "safety": 1-10（安全）, "comfort": 1-10（居住舒适）},
      "distance": {"airport_min": 数字（到机场分钟）, "station_min": 数字（到主要火车站分钟）, "spots_min": 数字（到当天主要景点分钟）},
      "pros": ["优点1", "优点2"],
      "cons": ["不足1"],
      "reason": "推荐理由（1-2句，要具体：为什么住这里）",
      "fit": "适合人群（如 情侣/带娃家庭/预算敏感型）",
      "stage": "适配行程阶段（如 全程 / 第1-2天 / 返程前一晚）"
    }
  ],
  "stay_pick": {"name": "优先推荐的那一个（必须是 stays 里的）", "why": "一句话结论：综合了哪几项、为什么它最合适"},
  "traffic": {
    "total_min": 数字（全程在途总耗时，分钟，含步行/车程/候车换乘）,
    "cost": 数字（全程交通花费，元）,
    "difficulty_overall": "轻松|适中|较费体力（三选一，取最难的一天）",
    "peak_note": "高峰波动说明，如 早晚高峰各加 20-30 分钟"
  },
  "alternatives": [
    {"option": "方案A（本方案）：一句话概括", "pros": "优点", "cons": "缺点", "chosen": true},
    {"option": "方案B：一句话概括另一种走法", "pros": "优点", "cons": "缺点", "chosen": false}
  ],
  "decision_basis": ["基于用户需求的决策依据1", "依据2", "依据3"],
  "budget_summary": { "交通": 0, "住宿": 0, "餐饮": 0, "门票": 0, "总计": 0 },
  "assumptions": ["你做的关键假设，如：未说明出发城市，按当地出发计算"]
}

硬性约束：
1. days 数量必须等于需求天数，day 从 1 开始连续编号；
2. 每天 items 4-6 条，时间从早上到晚上合理排布，符合需求的节奏（悠闲=每天3-4个点，紧凑=5-6个点）；
3. 同一天的地点必须地理上顺路（同城市内），不要上午东郊下午西郊来回折腾；
4. cost_estimate 与 budget_summary 数额要能对上；未给预算时按中低标准估算并在 assumptions 说明；
5. poi 只写真实存在的知名地点名（如 宽窄巷子、成都大熊猫繁育基地），绝对不要编造；
6. alternatives 必须给 2 个真实可行的不同走法并对比优劣，说明为什么最终选了本方案；
7. decision_basis 要明确对应到用户说过的需求（天数/偏好/预算/节奏），不许空话；
8. difficulty/transit/timing 是核心评估维度，必须给具体数字和依据：
   难度依据要写清步行距离、爬升或台阶、路况、换乘次数、是否需提前预约；
   交通要写清可用的地铁/公交/步行/自驾/打车组合与就近站点、停车条件；
   耗时要把步行接驳、候车、换乘分开算，并给出高峰时段的波动范围；
   数字必须自洽：步行按 5 公里/小时折（5 公里≈60 分钟，不是 300 分钟）；
   候车是"等车"的时间、不是坐车时间；三项加起来通常不超过 240 分钟；
9. stays 必须给 3 个不同档次（经济/中端/高端或民宿），覆盖不同预算；
   五项评分 1-10 且要和理由对得上（说"交通最方便"就不能给交通低分）；
   距离、价格、适合人群、适配阶段都要给具体值，不许空话；
10. 只输出 JSON。"""


def _to_spot_days(it: dict) -> dict:
    """输出形状统一：days[].items[]（地点名在 poi）→ days[].spots[]（地点名在 name）。

    下游的 M3 地图核实、前端行程面板、历史方案都以 spots/name 为契约。
    缺了这一步会出现"行程已生成但每天是空的"（v1.0 验收抓到的真缺陷）。
    没写 poi 的条目（如纯休息）不进 spots，避免核实环节拿空名乱匹配。
    """
    for day in it.get("days") or []:
        if day.get("spots"):
            continue  # 已经是 spots 形状（如重跑/历史数据），不重复加工
        spots = []
        # 模型偶尔会在 items 里塞嵌套数组等非字典元素，直接跳过，不让整次规划崩掉
        for item in [x for x in (day.get("items") or []) if isinstance(x, dict)]:
            name = str(item.get("poi") or item.get("name") or "").strip()
            if not name:
                continue
            spots.append({**item, "name": name})
        day["spots"] = spots
    return it


DIFFICULTY_LEVELS = ["轻松", "适中", "较费体力"]


def _as_int(v, default: int = 0) -> int:
    """把 AI 给的乱七八糟数字（字符串/小数/空）统一成正整数。"""
    try:
        return max(0, int(float(v)))
    except (TypeError, ValueError):
        return default


# 各耗时的合理上限（分钟）：模型偶尔给出"步行300分钟"这种离谱数字，
# 直接展示会误导用户，这里按常识封顶（封顶后再算合计，保证数字自洽）
_TIMING_CAPS = {"walk_min": 240, "ride_min": 240, "wait_min": 90,
                "transfers": 8, "peak_buffer_min": 60}


def _strip_label(s: str, labels: tuple[str, ...]) -> str:
    """去掉模型照抄提示词留下的标签（如"就近站点：永宁门站"→"永宁门站"）。"""
    s = str(s or "").strip()
    for lab in labels:
        if s.startswith(lab):
            s = s[len(lab):].strip()
    return s


def _ensure_traffic(it: dict) -> dict:
    """补齐"出行难度 / 交通可达性 / 在途耗时"三类字段（幂等）。

    为什么必须兜底：AI 偶尔漏字段，升级前存的老行程里压根没这些字段。
    缺了就按已有数据推算或给中性值，保证前端永远有东西可展示、不同城市之间可比。
    """
    total_min = 0
    hardest = 0
    for day in it.get("days") or []:
        t = day.get("timing") or {}
        walk = min(_as_int(t.get("walk_min")), _TIMING_CAPS["walk_min"])
        ride = min(_as_int(t.get("ride_min")), _TIMING_CAPS["ride_min"])
        wait = min(_as_int(t.get("wait_min")), _TIMING_CAPS["wait_min"])
        transfers = min(_as_int(t.get("transfers")), _TIMING_CAPS["transfers"])
        peak = min(
            _as_int(t.get("peak_buffer_min"), max(10, round((walk + ride + wait) * 0.2))),
            _TIMING_CAPS["peak_buffer_min"],
        )
        day["timing"] = {
            "walk_min": walk, "ride_min": ride, "wait_min": wait,
            "transfers": transfers, "peak_buffer_min": peak,
            "total_min": walk + ride + wait,
        }
        total_min += day["timing"]["total_min"]

        tr = day.get("transit") or {}
        modes = [str(m) for m in (tr.get("modes") or []) if str(m).strip()] or ["步行"]
        day["transit"] = {
            "modes": modes,
            "combo": _strip_label(tr.get("combo") or "、".join(modes), ("推荐组合：", "组合：")),
            "stations": [
                _strip_label(s, ("就近站点：", "站点："))
                for s in (tr.get("stations") or []) if str(s).strip()
            ],
            "parking": _strip_label(tr.get("parking") or "", ("停车条件：", "停车：")),
        }

        d = day.get("difficulty") or {}
        level = d.get("level") if d.get("level") in DIFFICULTY_LEVELS else ""
        basis = [str(x) for x in (d.get("basis") or []) if str(x).strip()]
        if not level:  # 没给就按步行量与换乘次数粗判
            if walk >= 120 or transfers >= 3:
                level = "较费体力"
            elif walk <= 45 and transfers <= 1:
                level = "轻松"
            else:
                level = "适中"
        if not basis:
            basis = [f"全天步行约 {walk} 分钟" if walk else "以车程为主，步行量小",
                     f"需换乘 {transfers} 次" if transfers else "基本无需换乘"]
        day["difficulty"] = {"level": level, "basis": basis}
        hardest = max(hardest, DIFFICULTY_LEVELS.index(level))

        # 每个点的接驳信息（items 与 spots 都补，老数据只有 spots）
        holders = [
            x for x in (day.get("items") or []) + (day.get("spots") or []) if isinstance(x, dict)
        ]
        for holder in holders:
            a = holder.get("access") or {}
            holder["access"] = {
                "mode": str(a.get("mode") or "步行"),
                "min": _as_int(a.get("min")),
                "note": str(a.get("note") or holder.get("transfer") or ""),
            }

    tf = it.get("traffic") or {}
    cost = tf.get("cost")
    if cost is None:
        cost = (it.get("budget_summary") or {}).get("交通") or 0
    it["traffic"] = {
        # 合计以"各天封顶后的数值之和"为准，保证全程耗时一定等于各天相加（自洽优先）
        "total_min": total_min,
        "cost": _as_int(cost),
        "difficulty_overall": (
            tf.get("difficulty_overall") if tf.get("difficulty_overall") in DIFFICULTY_LEVELS
            else DIFFICULTY_LEVELS[hardest]
        ),
        "peak_note": str(tf.get("peak_note") or f"早晚高峰各多预留约 {max(10, round(total_min * 0.2))} 分钟"),
    }
    return it


# 住宿五维评估（维度键, 中文名, 权重）：性价比与交通各占 1/4，安全 1/5，配套与舒适各 0.15
STAY_DIMS = (
    ("value", "性价比", 0.25),
    ("transit", "交通便利", 0.25),
    ("dining", "餐饮配套", 0.15),
    ("safety", "安全性", 0.20),
    ("comfort", "舒适度", 0.15),
)


def _score10(v, default: int = 7) -> int:
    """1-10 分，越界夹住（模型偶有给 0 或 100）。"""
    return max(1, min(10, _as_int(v, default)))


def _default_stay(city: str) -> dict:
    """模型没给住宿时的兜底条目：不编造酒店名，只给选址思路。"""
    return {
        "name": f"{city or '目的地'}核心商圈／地铁站周边（待选）",
        "area": city, "type": "待定",
        "price_min": 0, "price_max": 0, "price_range": "价格待询",
        "scores": {k: 7 for k, _l, _w in STAY_DIMS},
        "distance": {"airport_min": 0, "station_min": 0, "spots_min": 0},
        "pros": [], "cons": [],
        "reason": "本次未给出具体住宿，建议优先选核心商圈或地铁站周边，兼顾通勤与吃饭",
        "fit": "通用", "stage": "全程", "score_total": 7.0,
    }


def _ensure_stays(it: dict) -> dict:
    """补齐住宿推荐：五维评分、综合分、优先推荐（幂等）。

    综合分由代码按权重算并用于排序；★ 徽章跟随最终建议（stay_pick）——
    模型了解用户语境，可以选分数第二的更合适档位，但徽章与建议绝不允许自相矛盾。
    """
    city = str(it.get("city") or "").strip()
    stays: list[dict] = []
    for s in [x for x in (it.get("stays") or []) if isinstance(x, dict)]:
        name = str(s.get("name") or "").strip()
        if not name:
            continue
        sc = s.get("scores") or {}
        dist = s.get("distance") or {}
        lo = _as_int(s.get("price_min"))
        hi = _as_int(s.get("price_max"), lo)
        if hi < lo:
            lo, hi = hi, lo
        stay = {
            "name": name,
            "area": str(s.get("area") or ""),
            "type": str(s.get("type") or "住宿"),
            "price_min": lo,
            "price_max": hi,
            "price_range": f"{lo}-{hi} 元/晚" if (lo or hi) else "价格待询",
            "scores": {k: _score10(sc.get(k)) for k, _l, _w in STAY_DIMS},
            "distance": {k: _as_int(dist.get(k))
                         for k in ("airport_min", "station_min", "spots_min")},
            "pros": [str(x) for x in (s.get("pros") or []) if str(x).strip()],
            "cons": [str(x) for x in (s.get("cons") or []) if str(x).strip()],
            "reason": str(s.get("reason") or ""),
            "fit": str(s.get("fit") or "通用"),
            "stage": str(s.get("stage") or "全程"),
        }
        stay["score_total"] = round(
            sum(stay["scores"][k] * w for k, _l, w in STAY_DIMS), 2
        )
        stays.append(stay)

    if not stays:
        stays = [_default_stay(city)]
    stays.sort(key=lambda x: x["score_total"], reverse=True)

    pick = it.get("stay_pick") or {}
    name = str(pick.get("name") or "").strip()
    if name not in {s["name"] for s in stays}:
        name = stays[0]["name"]  # 指向不存在的条目时，以综合分最高者为准
    # ★ 徽章跟随最终建议（模型了解用户语境，可选分数第二的更合适档位），
    #   但绝不允许"徽章是 A、最终建议是 B"这种自相矛盾的展示
    for s in stays:
        s["recommended"] = (s["name"] == name)

    why = str(pick.get("why") or "").strip()
    it["stays"] = stays
    it["stay_pick"] = {
        "name": name,
        "why": why or "综合表现最均衡：性价比、交通便利与安全兼顾",
    }
    return it


async def generate_itinerary(collected):
    """生成行程 JSON。入参与出参结构和 Node 版一致。"""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"【用户需求】{json.dumps(collected, ensure_ascii=False)}\n请生成行程 JSON。",
        },
    ]
    reply = await chat(messages, temperature=0.7, timeout_ms=240000)
    it = extract_json(reply)
    # 城市补上（客房兜底文案要用；历史记录里也便于一眼看出是哪座城）
    it["city"] = str(it.get("city") or collected.get("destination") or collected.get("city") or "")

    # 结构校验：天数一致、每日有安排
    try:
        expect_days = int(collected.get("days") or 0)
    except (TypeError, ValueError):
        expect_days = 0
    days = it.get("days") if isinstance(it.get("days"), list) else None

    if expect_days > 0 and days is not None and len(days) != expect_days:
        # 天数不符 → 让模型重排一次（自检纠偏，不抛给用户）
        fix_reply = await chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"【用户需求】{json.dumps(collected, ensure_ascii=False)}\n"
                        f"【你上次的输出有问题】天数应为 {expect_days} 天，实际 {len(days)} 天。"
                        "请严格按天数重新输出 JSON。"
                    ),
                },
            ],
            temperature=0.4,
            timeout_ms=240000,
        )
        fixed = extract_json(fix_reply)
        fixed["city"] = str(fixed.get("city") or it.get("city") or "")
        fixed_days = fixed.get("days")
        if isinstance(fixed_days, list) and len(fixed_days) == expect_days:
            return _ensure_stays(_ensure_traffic(_to_spot_days(fixed)))
        raise RuntimeError(f"行程天数校验失败：应为 {expect_days} 天")

    if days is None or any(
        not isinstance(d.get("items"), list) or len(d.get("items")) == 0 for d in days
    ):
        raise RuntimeError("行程结构异常：某天没有安排")
    return _ensure_stays(_ensure_traffic(_to_spot_days(it)))
