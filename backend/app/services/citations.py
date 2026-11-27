"""把检索到的站内资料落到具体某一天、某个地点。

为什么用名字匹配而不是让模型标编号：

1. `SYSTEM_PROMPT` 是红线，加字段会让整份 JSON schema 变，校验全挂。
2. 让模型在每条安排里输出 `ref` 编号，格式错一个就整份重生成，代价太大。
3. 名字匹配是确定性的：同样的行程和资料，永远得到同样的引用结果，
   可测、可解释，也不会因为模型这一次"心情不同"就漏标。

匹配方向是「行程地点 → 资料正文」，不是反过来。一段 400 字的资料里可能
出现五个地名，它们都算这段资料的贡献，所以多个地点命中同一条资料是对的。
"""
from __future__ import annotations

import re

# 这些词只说明"这是个地点"，不说明"是资料里那个地点"。
# 拿它们去正文里做子串匹配，几乎每条资料都会命中。
_GENERIC = {
    "公园", "广场", "中心", "景区", "博物馆", "美术馆", "科技馆", "纪念馆",
    "动物园", "植物园", "古城", "古镇", "老街", "古街", "步行街", "商业街",
    "火车站", "汽车站", "机场", "地铁站", "酒店", "饭店", "餐厅", "小吃",
    "美食", "夜市", "市场", "学校", "大学", "医院", "商场", "观景台",
    "索道", "码头", "游客中心", "服务中心", "游客中心", "度假区",
}

_SUFFIXES = (
    "风景区", "景区", "公园", "博物馆", "美术馆", "科技馆", "纪念馆",
    "古镇", "古城", "步行街", "商业街", "老街", "古街", "广场",
    "大道", "车站", "码头", "索道", "观景台",
)

_BRACKET = re.compile(r"[（(][^）)]{0,24}[）)]")
_SPACE = re.compile(r"[\s\u3000·•\-—_/]+")


def _norm(text) -> str:
    """去掉括号注释、空白和连接符，只留可用于比对的字。"""
    value = _BRACKET.sub("", str(text or ""))
    return _SPACE.sub("", value).strip("《》\"'“”‘’")


def _aliases(name: str) -> list[str]:
    """一个地点名的可匹配写法。

    完整名只要不是"公园""博物馆"这种通用词就能用；去掉后缀的核心词
    必须至少 3 个字才允许做子串匹配——"人民公园"去掉后缀只剩"人民"，
    拿"人民"去正文里搜会把人民路、人民广场全捞进来。
    """
    full = _norm(name)
    if len(full) < 2:
        return []
    out = [] if full in _GENERIC else [full]
    for suffix in _SUFFIXES:
        if full.endswith(suffix) and len(full) - len(suffix) >= 3:
            core = full[: -len(suffix)]
            if core not in _GENERIC:
                out.append(core)
            break
    return out


def match_refs(name: str, references: list[dict], *, limit: int = 3) -> list[int]:
    """返回该地点对应的资料编号（1 起，对应提示词里的 [1][2]）。

    编号必须和 `_reference_block` 的顺序一致，所以这里不做任何重排或过滤，
    调用方传进来的 references 就是拼进提示词的那一份。
    """
    aliases = _aliases(name)
    if not aliases:
        return []
    hits: list[int] = []
    for index, ref in enumerate(references, 1):
        if not isinstance(ref, dict):
            continue
        title = _norm(ref.get("title"))
        content = str(ref.get("content") or "")
        text = f"{title} {content}"
        # 景点片段（source_type=spot）的标题就是景点名，标题和地点名互相包含
        # 基本可以认定是同一条；标题是章节名（交通、观光）时这条不成立。
        if title and len(title) >= 2 and (title in aliases or any(a == title for a in aliases)):
            hits.append(index)
            continue
        if any(alias in text for alias in aliases):
            hits.append(index)
        if len(hits) >= limit:
            break
    return hits


def attach_citations(itinerary: dict, references) -> dict:
    """给行程的每个地点和每天写上 ref_ids，并返回引用统计。

    幂等：重复调用会先清掉上一次的 ref_ids。地图核实会替换地点名，
    核实前后各跑一次，最终以核实后的名字为准。
    """
    stats = {"days": 0, "days_with_refs": 0, "spots": 0, "spots_with_refs": 0, "refs": 0}
    refs = [r for r in (references or []) if isinstance(r, dict)]
    if not isinstance(itinerary, dict) or not refs:
        return stats

    stats["refs"] = len(refs)
    for day in itinerary.get("days") or []:
        if not isinstance(day, dict):
            continue
        stats["days"] += 1
        day_hits: set[int] = set()
        for spot in day.get("spots") or []:
            if not isinstance(spot, dict):
                continue
            spot.pop("ref_ids", None)
            stats["spots"] += 1
            found = match_refs(str(spot.get("name") or ""), refs)
            if not found:
                continue
            spot["ref_ids"] = found
            day_hits.update(found)
            stats["spots_with_refs"] += 1
        if day_hits:
            day["ref_ids"] = sorted(day_hits)
            stats["days_with_refs"] += 1

    itinerary["citation_stats"] = stats
    return stats


def describe(stats: dict) -> str:
    """给规划日志用的半句话（调用方自己加"资料引用："前缀）。

    不夸大：一条都没落上就明说，避免日志看起来"用了资料"实际没用。
    """
    if not stats or not stats.get("refs"):
        return ""
    if not stats.get("spots_with_refs"):
        return "本次资料未直接落到具体安排"
    return (
        f"{stats['days_with_refs']}/{stats['days']} 天、"
        f"{stats['spots_with_refs']}/{stats['spots']} 个安排对应到站内资料"
    )
