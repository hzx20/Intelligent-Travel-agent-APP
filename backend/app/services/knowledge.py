"""站内知识库：切块、向量化与语义检索（RAG 的检索层）。

为什么不用独立向量库：当前语料是几千条片段，一次全量余弦排序在 SQLite 上
只要几毫秒；再起一个 Chroma/Qdrant 进程，对 2G 内存的服务器是纯负担，还会
多出 systemd 单元、健康检查和故障点。数据量涨到十万级再换，接口不用变。

三条对外契约（其余都是内部实现）：
- chunk_text(text, title)        切块，纯函数，不碰网络与数据库
- embed_texts(texts)             批量向量化，async，供灌库脚本用
- search(query, city, k, ...)    同步检索，供 asyncio.to_thread 调用

降级原则：检索是增强项。search 内部任何异常都吞掉返回空列表，不向上抛——
和「天气失败不阻断行程」是同一套设计。
"""
from __future__ import annotations

import logging
import re
import time

import httpx
import numpy as np
from cachetools import TTLCache

from app.core.circuit_breaker import get_breaker
from app.core.config import settings
from app.core.llm import ZHIPU_BASE_URL
from app.core.metrics import SEARCH_LATENCY
from app.core.provider_budget import async_provider_slot, provider_slot
from app.services.rerank import rerank_scores

logger = logging.getLogger("uvicorn.error")

EMBED_MODEL = "embedding-3"
EMBED_DIM = 1024                  # 智谱 embedding-3 支持 256/512/1024/2048，取 1024
EMBED_URL = f"{ZHIPU_BASE_URL}/embeddings"
EMBED_BATCH = 16                  # 单批上限，别一次塞几百条把接口拖超时
EMBED_TIMEOUT = 8.0               # 生成前多等 8 秒还能接受，再久就该降级了
_RETRY = 2                        # 失败重试 2 次（共 3 次尝试）

# 同一 query 反复检索（用户连点、重试、多标签页）不该重复付 embedding 费用
_cache: TTLCache = TTLCache(maxsize=256, ttl=600)

# 缓存命中计数（进程生命周期累计）：给 /api/usage/summary 和 /metrics 提供
# 「缓存命中率」。只增不减；clear_cache() 清条目不清计数（命中率是长期指标）
_cache_hits = 0
_cache_misses = 0


def cache_stats() -> dict:
    """检索缓存的命中统计：命中率 = hits / (hits + misses)，无请求时为 0。"""
    total = _cache_hits + _cache_misses
    return {
        "hits": _cache_hits,
        "misses": _cache_misses,
        "hit_rate": round(_cache_hits / total, 4) if total else 0.0,
    }


# ---------------------------------------------------------------- 切块

def _clean(text: str) -> str:
    """统一换行、压掉行内多余空白，保留段落之间的空行。"""
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(lines).strip()


# ---------------------------------------------------------- 事实时效性清洗
#
# 维基导游正文里大量「门票 60 元」「人均 50 元」「更新日期 2019-05」这类断言。
# 价格和营业时间是最容易过期、也最容易被模型当真写进行程的东西：
# 模型看到「人均 60 元左右」会照抄，用户到店发现涨到 120，比不写更糟。
# 处理原则是删数字、留结构：把「门票 60 元」变成「门票」，让模型知道
# 这里有门票，但不敢写具体金额。

_PRICE = re.compile(
    r"[¥￥]\s*\d+(?:\.\d+)?(?:\s*[-~至]\s*\d+(?:\.\d+)?)?"
    r"|\d+(?:\.\d+)?\s*[-~至]\s*\d+(?:\.\d+)?\s*(?:元|块钱|块)(?:\s*(?:/|每)\s*[人晚天间次])?"
    r"|\d+(?:\.\d+)?\s*(?:元|块钱|块)(?:\s*(?:/|每)\s*[人晚天间次])?"
)
_STALE_DATE = re.compile(
    r"(?:最后)?(?:更新|编辑|修订)日期?\s*[:：]?\s*"
    r"\d{4}\s*[-年./]\s*\d{1,2}\s*(?:[-月./]\s*\d{1,2}\s*[日号]?)?\s*月?"
)
_ORPHAN = re.compile(r"[（(]\s*[）)]|，\s*[，。；]|、\s*[。；]")
# 维基导游的 listing 标记：💰 价格、📞 电话。
# 「💰 0」是这里最坑的一种：模型看到就当真，把要门票的景区写成 0 元，
# 整份行程的门票预算变成 0。电话同理，是过期信息且对规划没用。
# 营业时间（🕘）保留——它对排程有用，模型也会自己标「以现场为准」。
_MONEY_TAG = re.compile(r"💰\s*[^。\n]{0,40}[。\n]?")
_PHONE_TAG = re.compile(r"📞\s*[^。\n]{0,40}[。\n]?")
# 「人均 50 元左右」删掉数字后剩「人均 左右」，把量词尾巴一并收掉
_PRICE_RESIDUE = re.compile(
    r"(人均|门票|票价|价格|费用|花费|预算)\s*(?:约|大约|大概|差不多)?\s*"
    r"(?:左右|上下|起|以内|以下|出头)"
)


def stale_fact_hits(text: str) -> int:
    """一段文本里有多少处价格/日期断言，用于灌库时统计与调参。"""
    value = str(text or "")
    return (
        len(_PRICE.findall(value))
        + len(_STALE_DATE.findall(value))
        + len(_MONEY_TAG.findall(value))
    )


def strip_stale_facts(text: str) -> str:
    """删掉易过期的具体价格与更新日期，保留句子结构。

    只做删除，不改写语义：删完可能出现「门票」后面直接接逗号，
    用 _ORPHAN 收一下尾，不额外造词。
    """
    value = str(text or "")
    if not value:
        return ""
    value = _MONEY_TAG.sub("", value)
    value = _PHONE_TAG.sub("", value)
    value = _STALE_DATE.sub("", value)
    value = _PRICE.sub("", value)
    value = _PRICE_RESIDUE.sub(r"\1", value)
    value = _ORPHAN.sub(lambda m: "，" if "，" in m.group(0) else "", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r"\s+([，。；：、])", r"\1", value)
    value = re.sub(r"([，。；：])\s*([，。；：])", r"\1", value)
    # 中文之间不该留空格：删掉价格后常剩「预算 够了」这种缝
    value = re.sub(r"(?<=[\u4e00-\u9fa5])\s+(?=[\u4e00-\u9fa5])", "", value)
    return value.strip(" ，、")


def _sentences(block: str) -> list[str]:
    """按中英文句末标点断句，并保留标点（切块后读起来仍是完整句子）。"""
    parts = re.split(r"(?<=[。！？!?；;])", block)
    return [p.strip() for p in parts if p.strip()]


def _hard_split(block: str, size: int) -> list[str]:
    """实在没有标点的长串（表格、地址串）按长度硬切，保证不超长。"""
    return [block[i:i + size] for i in range(0, len(block), size)]


def _tail(text: str, overlap: int) -> str:
    """取上一块的结尾做重叠，尽量从句子边界开始，避免半个句子当开头。"""
    if overlap <= 0 or not text:
        return ""
    tail = text[-overlap:]
    boundary = re.search(r"[。！？!?；;\n]", tail)
    if boundary and boundary.end() < len(tail):
        tail = tail[boundary.end():]
    return tail.strip()


def chunk_text(text: str, title: str = "", *, size: int = 400, overlap: int = 80) -> list[str]:
    """把长文本切成片段：优先按空行分段，段落超长再按句号切。

    - 相邻片段保留 overlap 个字符的重叠，避免一句话被切断在两块里；
    - title 只由调用方记录到 title 字段，不拼进正文——正文里重复标题会稀释
      向量语义（同一篇攻略每个片段都带「成都」反而让城市词失去区分度）。
    """
    text = _clean(text)
    if not text:
        return []
    size = max(50, int(size))
    overlap = max(0, min(int(overlap), size // 2))

    # 单个换行也是一条独立信息（维基导游的每个 listing 占一行）。
    # 按行切成单元再装箱，能避免一个片段横跨三四个景点、把语义摊薄。
    paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    units: list[str] = []
    for para in paragraphs:
        if len(para) <= size:
            units.append(para)
        else:
            units.extend(_sentences(para))

    chunks: list[str] = []
    buf = ""
    for unit in units:
        pieces = [unit] if len(unit) <= size else _hard_split(unit, size)
        for piece in pieces:
            if not buf:
                buf = piece
            elif len(buf) + 1 + len(piece) <= size:
                buf = f"{buf}\n{piece}"
            else:
                chunks.append(buf)
                tail = _tail(buf, overlap)
                merged = f"{tail}\n{piece}" if tail else piece
                # 重叠加上新句子超长时放弃重叠：片段长度上限优先于连贯性
                buf = merged if len(merged) <= size else piece
    if buf:
        chunks.append(buf)
    return [c.strip() for c in chunks if c.strip()]


# ---------------------------------------------------------------- 向量化

def _api_key() -> str:
    if not settings.zhipu_api_key:
        raise RuntimeError("缺少 ZHIPU_API_KEY，无法调用 embedding 接口")
    return settings.zhipu_api_key


def _payload(texts: list[str]) -> dict:
    return {"model": EMBED_MODEL, "input": texts, "dimensions": EMBED_DIM}


def _vectors(data: dict, expected: int) -> list[list[float]]:
    """校验接口返回的形状，避免把残缺结果写进库里。"""
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list) or len(rows) != expected:
        raise RuntimeError("embedding 接口返回条数不符")
    ordered = sorted(rows, key=lambda r: r.get("index", 0))
    out = []
    for row in ordered:
        vec = row.get("embedding")
        if not isinstance(vec, list) or len(vec) != EMBED_DIM:
            raise RuntimeError("embedding 维度不符")
        out.append(vec)
    return out


async def _embed_batch_async(client: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
    last: Exception | None = None
    for attempt in range(_RETRY + 1):
        try:
            async with async_provider_slot("chat"):
                resp = await client.post(
                    EMBED_URL,
                    headers={"Authorization": f"Bearer {_api_key()}"},
                    json=_payload(texts),
                )
            resp.raise_for_status()
            return _vectors(resp.json(), len(texts))
        except Exception as exc:  # 网络、限流、返回结构异常都走同一套重试
            last = exc
            if attempt < _RETRY:
                import asyncio

                await asyncio.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"embedding 调用失败：{type(last).__name__}") from last


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量向量化。单批不超过 16 条；单批失败重试 2 次，全失败抛异常。

    抛异常是有意的：灌库脚本必须知道「这批没写成功」，不能悄悄写入半套向量。
    """
    texts = [" ".join(str(t or "").split())[:2000] for t in texts]
    if not texts:
        return []
    out: list[list[float]] = []
    timeout = httpx.Timeout(EMBED_TIMEOUT, connect=4.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        for start in range(0, len(texts), EMBED_BATCH):
            batch = texts[start:start + EMBED_BATCH]
            out.extend(await _embed_batch_async(client, batch))
            if start + EMBED_BATCH < len(texts):
                # 配额开着时也能给其他调用让路；正常情况只是几十毫秒的间隔
                import asyncio

                await asyncio.sleep(0.2)
    return out


def _embed_sync(texts: list[str]) -> list[list[float]]:
    """同步版：search() 在线程里跑，用同步 httpx 最省事。

    不在 search 里 asyncio.run()：调用方可能已经处在事件循环里（例如测试或
    未来的同步封装），嵌套事件循环会直接报错。
    """
    texts = [" ".join(str(t or "").split())[:2000] for t in texts]
    if not texts:
        return []
    last: Exception | None = None
    timeout = httpx.Timeout(EMBED_TIMEOUT, connect=4.0)
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        for attempt in range(_RETRY + 1):
            try:
                with provider_slot("chat"):
                    resp = client.post(
                        EMBED_URL,
                        headers={"Authorization": f"Bearer {_api_key()}"},
                        json=_payload(texts),
                    )
                resp.raise_for_status()
                return _vectors(resp.json(), len(texts))
            except Exception as exc:
                last = exc
                if attempt < _RETRY:
                    time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"embedding 调用失败：{type(last).__name__}") from last


# ---------------------------------------------------------------- 检索

# 口语 → 书面词：这是单点收益最高的一步。用户说「带娃」「少走路」，
# 攻略正文写的是「亲子」「步行距离短」，只做向量匹配会差一截。
# 否定句要单独处理：embedding 对「不想爬山」的理解几乎等于「爬山」，
# 必须把否定短语整体换成它真正想要的属性（平路/缆车），不能只追加同义词。
_NEGATIONS = {
    "不想爬山": "平路 缆车 少台阶 轻松游览",
    "怕爬山": "平路 缆车 少台阶 轻松游览",
    "不想走路": "步行距离短 代步 少步行",
    "走不动": "步行距离短 代步",
    "不想太累": "轻松 悠闲 少步行",
    "不要爬山": "平路 缆车 少台阶",
}

_SYNONYMS = {
    "带娃": "亲子 儿童",
    "孩子": "亲子 儿童",
    "小孩": "亲子 儿童",
    "宝宝": "亲子 婴幼儿",
    "老人": "长辈 无障碍 休憩 湖边 公园 游船 博物馆",
    "父母": "长辈 无障碍 休憩 湖边 公园",
    "少走路": "步行距离短 少步行",
    "室内为主": "室内场馆 博物馆 展览",
    "下雨": "雨天 室内 备选",
    "美食": "餐饮 小吃 特色菜",
    "预算": "价格 花费 省钱",
    "便宜": "价格低 经济 实惠",
    "怎么去": "交通 公交 地铁 路线",
}


def expand_query(query: str) -> str:
    """口语改写：先处理否定句（替换），再追加书面同义词。

    返回的是「给 embedding 看的 query」，不用于展示——原话仍保留在 collected 里。
    """
    text = str(query or "").strip()
    if not text:
        return text
    for phrase, replacement in _NEGATIONS.items():
        if phrase in text:
            text = text.replace(phrase, replacement)
    extra = []
    for word, written in _SYNONYMS.items():
        if word in text and written not in text:
            extra.append(written)
    return f"{text} {' '.join(extra)}" if extra else text


# ---------------------------------------------------------- LLM 查询改写（三路对比的第二路）
#
# 词典改写（expand_query）覆盖的是「预先想到的口语」，覆盖面受词典限制；
# LLM 改写理论上能处理任意口语。是否值得多付一次 LLM 调用（延迟 + token），
# 由 eval_retrieval.py --rewrite dict|llm|none 三路对比拿数字说话。

_REWRITE_SYSTEM = (
    "你是旅游检索查询改写器。把用户输入改写成适合语义检索的查询："
    "1) 把口语说法换成旅游攻略正文常用的书面说法（例：带娃→亲子 儿童；不想爬山→平路 缆车 少台阶）；"
    "2) 保留城市与具体地名，补充正文里可能出现的同义词；"
    "3) 只输出改写后的查询本身，一行，60 字以内，不要解释、不要引号。"
)

# 独立熔断器：改写挂了不能连带把 chat/rerank 的熔断状态搅浑
_rewrite_breaker = get_breaker("rewrite")

# 改写重试 1 次（共 2 次尝试）：改写是锦上添花，3 次重试 × 5s 超时会把
# 检索拖到不可接受；与 rerank.py 的重试强度保持一致
_REWRITE_RETRY = 1

# 改写调用的累计统计（进程生命周期）：给 eval 报 token 与延迟，给 /metrics 报调用量
_rewrite_stats = {
    "calls": 0, "failures": 0,
    "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
    "latency_ms": 0,
}


def rewrite_stats() -> dict:
    """LLM 改写调用的累计统计快照。"""
    return dict(_rewrite_stats)


def expand_query_llm(query: str) -> str | None:
    """用 GLM 把口语查询改写成检索友好的查询（同步接口，供 search 线程调用）。

    返回改写文本；失败/熔断/未配 key 返回 None，调用方自己决定回退目标
    （eval 的 llm 臂回退到原 query，保持三臂对比纯净）。绝不抛异常。
    """
    try:
        q = str(query or "").strip()
        if not q or not settings.zhipu_api_key:
            return None
        payload = {
            "model": settings.glm_model,
            "temperature": 0.2,  # 改写要稳定，不要发散
            "messages": [
                {"role": "system", "content": _REWRITE_SYSTEM},
                {"role": "user", "content": q},
            ],
        }
        timeout = httpx.Timeout(5.0, connect=4.0)
        last: Exception | None = None
        for attempt in range(_REWRITE_RETRY + 1):
            started = time.perf_counter()
            try:
                if not _rewrite_breaker.allow():
                    return None
                with provider_slot("chat"):
                    # 复用 chat 槽位：同样是智谱调用，共享限流节奏
                    with httpx.Client(timeout=timeout, trust_env=False) as client:
                        resp = client.post(
                            f"{ZHIPU_BASE_URL}/chat/completions",
                            headers={"Authorization": f"Bearer {settings.zhipu_api_key}"},
                            json=payload,
                        )
                    resp.raise_for_status()
                data = resp.json()
                choices = data.get("choices") or [{}]
                text = str(choices[0].get("message", {}).get("content") or "").strip()
                if not text:
                    raise RuntimeError("改写返回为空")

                usage = data.get("usage") or {}

                def _int(key: str) -> int:
                    try:
                        return int(usage.get(key) or 0)
                    except (TypeError, ValueError):
                        return 0

                _rewrite_stats["calls"] += 1
                _rewrite_stats["prompt_tokens"] += _int("prompt_tokens")
                _rewrite_stats["completion_tokens"] += _int("completion_tokens")
                _rewrite_stats["total_tokens"] += _int("total_tokens")
                _rewrite_stats["latency_ms"] += int((time.perf_counter() - started) * 1000)
                _rewrite_breaker.record_success()
                # 清洗：剥引号/句号尾巴、压空白、限长——模型偶尔会带礼貌用语
                text = text.strip(" \t\r\n\"'「」『』。.")
                return " ".join(text.split())[:120]
            except Exception as exc:
                last = exc
                _rewrite_breaker.record_failure()
                if attempt < _REWRITE_RETRY:
                    time.sleep(0.4 * (attempt + 1))
        # failures 按「整次调用」计（与 calls 同口径）：重试尝试的失败只进熔断器
        _rewrite_stats["failures"] += 1
        logger.warning("LLM 改写失败（%s），按调用方的回退策略继续", type(last).__name__)
        return None
    except Exception:
        logger.exception("LLM 改写模块异常")
        return None


def _city_match(left: str, right: str) -> bool:
    """城市比较放宽到包含关系：「成都」要能匹配「成都市」，反之亦然。"""
    a = str(left or "").strip()
    b = str(right or "").strip()
    if not a or not b:
        return False
    return a == b or a in b or b in a


# 关键词打分的停用词：这些词几乎出现在每条 query 里，匹配上也不代表相关
_STOP = {
    "推荐", "怎么", "怎样", "什么", "哪里", "去哪", "多少", "可以", "值得",
    "两天", "三天", "四天", "五天", "一天", "几天", "怎么玩", "玩什么",
}


def _terms(query: str) -> list[str]:
    """粗粒度分词：按空白/标点切，长词再补 2 字片段。

    不引入 jieba（约 20MB、还要维护词典）：检索 query 是短句，按标点切开
    已经够用；「室内为主」这类四字词补上「室内」二字片段，才能命中正文里的
    「室内场馆」。片段只用于加分，不会影响向量主排序。
    """
    out: list[str] = []
    for part in re.split(r"[\s，,。、；;：:/|·]+", str(query or "")):
        part = part.strip()
        if len(part) < 2 or part in _STOP:
            continue
        out.append(part)
        if len(part) >= 4:
            out.extend(part[i:i + 2] for i in range(len(part) - 1))
    # 去重但保留顺序，方便调试
    seen, unique = set(), []
    for term in out:
        if term not in seen and term not in _STOP:
            seen.add(term)
            unique.append(term)
    return unique


def _keyword_score(terms: list[str], text: str) -> float:
    """关键词覆盖率（按词长加权）：长词命中比二字片段更有说服力。"""
    if not terms:
        return 0.0
    total = sum(len(t) for t in terms)
    hit = sum(len(t) for t in terms if t in text)
    return hit / total if total else 0.0


# 章节先验：同样的相似度，观光/景点内容比公交时刻表更该进提示词。
# 反例是「少走路」——它在向量空间里离「公交/地铁」很近（少走路=坐车），
# 但用户要的是「走路少的景点」，不是一份公交线路图。这是纯向量检索的盲区，
# 用一个显式的意图判断补上：用户没问交通时，交通章节降权。
_TRANSPORT_SECTIONS = (
    "交通", "抵达", "公交", "轨道", "铁路", "航空", "客车", "自驾",
    "出租车", "自行车", "地铁", "机场", "车站", "出行",
)
_PLACE_SECTIONS = (
    "观光", "景点", "公园", "博物馆", "活动", "游览", "古城", "古镇",
    "海滩", "湖", "山", "街区", "动物园", "植物园",
)
_TRANSPORT_ASK = ("怎么去", "交通", "地铁", "公交", "机场", "车站", "打车", "高铁", "火车", "轮渡")
_INDOOR_ASK = ("室内", "下雨", "雨天", "避雨")
_INDOOR_HINTS = ("博物馆", "展览馆", "美术馆", "科技馆", "纪念馆", "室内", "馆内", "剧院")
_OUTDOOR_HINTS = ("公园", "山", "湖", "海滩", "沙滩", "徒步", "登山", "户外", "广场", "骑行")


def _intent_adjust(source_type: str, title: str, content: str, query: str) -> float:
    """意图修正：按来源、章节标题、室内/户外属性做小幅加减分。

    幅度控制在 ±0.07 以内，只影响临界排序，不改变向量主排序。
    """
    score = 0.0
    if any(word in query for word in _TRANSPORT_ASK):
        score += 0.0  # 用户就是在问交通，别压交通章节
    elif source_type == "spot":
        score += 0.02  # 景点条目天然是「去哪儿玩」的候选
    elif any(word in title for word in _TRANSPORT_SECTIONS):
        score -= 0.05
    elif any(word in title for word in _PLACE_SECTIONS):
        score += 0.02

    if any(word in query for word in _INDOOR_ASK):
        head = f"{title} {content[:160]}"
        if any(word in head for word in _INDOOR_HINTS):
            score += 0.04
        elif any(word in head for word in _OUTDOOR_HINTS):
            score -= 0.03
    return score


def _load_rows(city: str):
    """读取候选片段。单独抽出来，测试可以直接替换，不必碰数据库。"""
    from sqlalchemy import text as sql_text

    from app.db.database import engine

    sql = (
        "SELECT id, source_type, source_id, city, title, content, source_url, embedding "
        "FROM knowledge_chunks WHERE embedding IS NOT NULL"
    )
    params: dict = {}
    if city:
        # SQL 先粗筛（成都 / 成都市 都能命中），避免把全库 4KB 向量都读进内存
        sql += " AND city <> '' AND (city LIKE :like OR :city LIKE '%' || city || '%')"
        params = {"like": f"%{city}%", "city": city}
    with engine.connect() as conn:
        return conn.execute(sql_text(sql), params).fetchall()


def _score(query_vec: np.ndarray, rows) -> list[tuple[float, object]]:
    """余弦相似度。维度不一致的历史片段直接跳过（换过模型时不至于整体崩）。"""
    vectors, kept = [], []
    for row in rows:
        raw = row.embedding
        if not raw:
            continue
        vec = np.frombuffer(raw, dtype=np.float32)
        if vec.size != query_vec.size:
            continue
        vectors.append(vec)
        kept.append(row)
    if not vectors:
        return []
    matrix = np.vstack(vectors)
    q = query_vec / (np.linalg.norm(query_vec) + 1e-9)
    norms = np.linalg.norm(matrix, axis=1) + 1e-9
    scores = (matrix @ q) / norms
    return list(zip(scores.tolist(), kept))


def _rerank_doc(row) -> str:
    """给 rerank 接口看的候选文本：标题 + 正文压成一行并截断。

    截到 400 字符：chunk_text 的片段上限就是 400，正常情况下不会截；
    超长只可能来自历史数据，截断保证永不超 rerank 的 4K 上下文。
    """
    return " ".join(f"{row.title or ''} {row.content or ''}".split())[:400]


def _rerank_select(original_query: str, scored: list, k: int) -> list[tuple]:
    """两阶段检索的第二阶段：cross-encoder 精排选最终 Top-K。

    第一阶段（混合检索）按「快但糙」的分数把候选池捞到 rerank_pool 个；
    第二阶段用智谱托管 rerank 按语义相关性重排，取 Top-K。
    返回 [(混合分, row, rerank分或None)]，只调一次 rerank 接口。

    与第一阶段的取舍关系：
    - 候选池不看 min_score 阈值——被阈值卡掉的「正确但分低」片段正是
      rerank 要捞回来的对象，先卡阈值就把重排的意义卡没了；
    - 相关性 floor（rerank_min_score）由 rerank 模型把关质量，
      比混合分的阈值更可靠；
    - 全部低于 floor 或 rerank 失败时回退混合排序 Top-K：可用性优先，
      重排只改进排序，绝不减少结果、绝不抛异常。
    """
    try:
        pool = scored[: max(k, int(settings.rerank_pool))]
        if len(pool) <= 1 or k <= 1:
            return [(final, row, None) for final, row in pool[:k]]
        ordered = rerank_scores(original_query, [_rerank_doc(row) for _, row in pool])
        if ordered is None:
            return [(final, row, None) for final, row in pool[:k]]
        floor = float(settings.rerank_min_score)
        selected = []
        for idx, rel in ordered:
            if rel >= floor:
                final, row = pool[idx]
                selected.append((final, row, rel))
            if len(selected) >= k:
                break
        # 候选全被 floor 判为不相关时保底返回混合排序头几条，别把结果掏空
        if not selected:
            return [(final, row, None) for final, row in pool[:k]]
        return selected
    except Exception:
        logger.exception("rerank 阶段异常，回退混合排序")
        return [(final, row, None) for final, row in scored[:k]]


def search(
    query: str,
    city: str = "",
    k: int = 5,
    min_score: float = 0.35,
    rerank: bool | None = None,
    rewrite: str = "dict",
) -> list[dict]:
    """同步检索：城市硬过滤 → 查询改写 → 向量 + 关键词 + 意图修正 → （可选）精排 → Top-K。

    rerank 参数三态：None（默认）跟随配置 settings.rerank_enabled（默认关）；
    True / False 显式开关，评测脚本用。原四参调用完全兼容。
    rewrite 参数三态（默认 "dict" 保持现行为）：
    - "dict"：词典改写（expand_query，口语→书面同义词，零 LLM 成本）
    - "llm" ：LLM 改写（expand_query_llm，失败回退原 query，保持评测臂纯净）
    - "none"：不改写，原 query 直接送 embedding（对照组）

    返回 [{"source_type","source_id","city","title","content","source_url","score"}]
    score 是混合分（0.8 向量 + 0.2 关键词 + 意图小幅修正），不再等于纯余弦；
    rerank 生效时结果按 rerank 相关性排序，score 仍是混合分，另带
    rerank_score 字段（rerank 未生效时不带该字段，保持旧返回形状不变）。
    任何异常返回空列表（记服务端日志），绝不向上抛。
    """
    global _cache_hits, _cache_misses
    started = time.perf_counter()
    try:
        query = str(query or "").strip()
        if not query:
            return []
        k = max(1, int(k))
        use_rerank = settings.rerank_enabled if rerank is None else bool(rerank)
        mode = str(rewrite or "dict").lower()
        if mode not in ("dict", "llm", "none"):
            mode = "dict"
        # rerank 开关与改写模式都进缓存键：不同组合互不串结果
        key = (query, str(city or ""), k, float(min_score), bool(use_rerank), mode)
        cached = _cache.get(key)
        if cached is not None:
            _cache_hits += 1
            return [dict(item) for item in cached]
        _cache_misses += 1

        rows = _load_rows(str(city or ""))
        if city:
            # SQL 的 LIKE 只是粗筛，这里再做一次语义确认：避免「成都道」这类
            # 恰好包含城市名的地址被当成城市匹配
            rows = [row for row in rows if _city_match(row.city, city)]
        if not rows:
            return []  # 空库不调接口：省一次 8 秒超时的风险

        # 查询改写三路：dict（默认，与历史行为一致）/ llm（失败回退原 query）/
        # none（对照组：原 query 直接送 embedding）
        if mode == "dict":
            rewritten = expand_query(query)
        elif mode == "llm":
            rewritten = expand_query_llm(query) or query
        else:
            rewritten = query
        query_vec = np.asarray(_embed_sync([rewritten])[0], dtype=np.float32)
        # 混合检索：向量分 0.8 + 关键词覆盖率 0.2。
        # 纯向量对专有名词（洪崖洞、漓江）和小语料不够稳，关键词分把它们捞回来；
        # 关键词只做加分项，所以「带娃」这类正文里没有的口语不会误伤排序。
        # 权重不按 0.7/0.3：中文 embedding 的余弦多在 0.45-0.65，向量分再乘 0.7
        # 会把「带5岁孩子 少走路」这类长偏好整体压到阈值以下（实测直接返回空）。
        # 关键词分作为 0.2 的加分项，既能拉开专有名词的差距，又不动主排序。
        # 注意用改写后的 query 取词：否则「不想爬山」里的「爬山」二字会反过来
        # 给登山线路加分，把否定意图抵消掉。
        terms = _terms(rewritten)
        scored = []
        for vec_score, row in _score(query_vec, rows):
            kw = _keyword_score(terms, f"{row.title or ''} {row.content or ''}")
            final = 0.8 * vec_score + 0.2 * kw
            final += _intent_adjust(row.source_type, row.title or "", row.content or "", rewritten)
            scored.append((final, row))
        scored.sort(key=lambda item: item[0], reverse=True)

        if use_rerank:
            # 两阶段：精排结果自带 rerank 分数，不需要二次调用
            selected = _rerank_select(query, scored, k)
        else:
            # 原路径（不重排）：阈值过滤后取 Top-K，行为与历史版本逐字节一致
            selected = [(final, row, None) for final, row in scored if final >= min_score][:k]

        out = []
        for final, row, rel in selected[:k]:
            item = {
                "source_type": row.source_type,
                "source_id": row.source_id,
                "city": row.city,
                "title": row.title,
                "content": row.content,
                "source_url": row.source_url,
                "score": round(float(final), 4),
            }
            if rel is not None:
                item["rerank_score"] = round(float(rel), 4)
            out.append(item)
        _cache[key] = out
        return [dict(item) for item in out]
    except Exception:
        logger.exception("知识库检索失败，按无资料继续生成")
        return []
    finally:
        # 成功与失败都计入延迟直方图：失败路径的耗时同样反映服务健康度
        SEARCH_LATENCY.observe(time.perf_counter() - started)


def clear_cache() -> None:
    """灌库后清缓存，否则 10 分钟内还在用旧结果。"""
    _cache.clear()


def stats() -> dict:
    """给脚本和排障用：片段总数与已向量化数量。"""
    from sqlalchemy import text as sql_text

    from app.db.database import engine

    with engine.connect() as conn:
        total = conn.execute(sql_text("SELECT COUNT(*) FROM knowledge_chunks")).scalar() or 0
        ready = conn.execute(
            sql_text("SELECT COUNT(*) FROM knowledge_chunks WHERE embedding IS NOT NULL")
        ).scalar() or 0
        by_type = dict(
            conn.execute(
                sql_text("SELECT source_type, COUNT(*) FROM knowledge_chunks GROUP BY source_type")
            ).fetchall()
        )
    return {"total": int(total), "embedded": int(ready), "by_type": by_type}


def city_spots(city: str, limit: int = 40) -> list[dict]:
    """同城景点候选，只给名字/区县/标签，用来给「一天只有一两个点」补位。

    不走向量检索：补位要的是「这个城市还有哪些能去的地方」，不是语义相似度。
    优先取标签里带风景名胜/景点的，再按 id 排，保证同一城市每次结果稳定
    （不稳定的话用户刷新一次，行程里会冒出不同的补充点）。
    """
    from app.db.database import SessionLocal
    from app.db.models import Spot

    name = str(city or "").strip()
    if not name:
        return []
    try:
        with SessionLocal() as db:
            rows = (
                db.query(Spot)
                .filter(Spot.city.like(f"%{name}%"))
                .order_by(Spot.id)
                .limit(max(1, int(limit)))
                .all()
            )
    except Exception:
        return []

    out = []
    for row in rows:
        tags = str(row.tags or "")
        district = str(row.district or "").strip()
        # 县级市（都江堰市、彭州市）离主城区通常几十公里，补位时排在市区后面：
        # 把都江堰补进「动物园 + 塔子山」那天，路线会横穿整个成都平原。
        remote = district.endswith("市") and district != name and name not in district
        out.append(
            {
                "name": str(row.name or "").strip(),
                "district": district,
                "tags": tags,
                "score": 0 if remote else (2 if ("风景名胜" in tags or "景点" in tags) else 1),
            }
        )
    # 市区景点优先，远郊最后；同档保持 id 顺序，保证结果稳定
    out.sort(key=lambda item: -item["score"])
    return [item for item in out if item["name"]]
