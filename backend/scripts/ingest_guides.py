"""把中文维基导游正文灌进知识库（Day 3：让这个功能真正成为 RAG）。

用法（在 backend/ 下）：
    python scripts/ingest_guides.py

要点：
- 正文授权为 CC BY-SA 4.0，source_url 必须存（指向固定版本 oldid，内容不会被后续编辑改掉）
- source_id 用 MediaWiki 的 pageid：稳定、唯一，重跑不会漂移
- 幂等：先删 source_type='guide' 再整批写入；抓取/向量化全部成功才动库
- 限速：Wikimedia 对匿名请求有速率限制（实测直接连发会 429），按城市串行 + 间隔
"""
import asyncio
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

import httpx
import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from sqlalchemy import delete  # noqa: E402

from app.db.database import SessionLocal, init_db  # noqa: E402
from app.db.models import KnowledgeChunk  # noqa: E402
from app.services import knowledge  # noqa: E402
from app.services.wikivoyage import LICENSE_URL  # noqa: E402

ENDPOINT = "https://zh.wikivoyage.org/w/api.php"
AGENT = "IntelligentTravelAgent/1.0 (https://134.175.137.18/travel/; RAG ingest)"

# 与产品定位对齐的 20 个目的地。小城市条目可能只有几百字，抓不到就跳过并打印原因。
CITIES = [
    "成都", "杭州", "西安", "厦门", "北京", "上海", "重庆", "长沙", "青岛", "大理",
    "丽江", "苏州", "南京", "广州", "深圳", "武汉", "昆明", "桂林", "哈尔滨", "三亚",
]

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
         "meta", "param", "source", "track", "wbr"}
_SKIP = {"script", "style", "sup", "table", "noscript"}
_BLOCKS = {"p", "li", "dd", "dt", "blockquote", "div", "tr"}
_DROP_SECTIONS = {
    "目录", "外部链接", "参考资料", "注释", "相关条目",
    # 与「玩什么」无关，留着只会稀释检索结果
    "学习", "工作", "领事馆", "下一站",
}


class _Sections(HTMLParser):
    """把 MediaWiki 输出拆成 (小节标题, 正文) 列表。

    只认 mw-parser-output 里的内容；表格整块丢弃——维基导游的表格主要是
    气候图和「下一站」导航框，对行程语义没有贡献，反而会把无关城市名带进来。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.inside = False
        self.skip = 0
        self.heading = None
        self.heading_parts: list[str] = []
        self.buf: list[str] = []
        self.sections: list[tuple[str, str]] = []
        self.title = "简介"

    # -- 内部工具 --
    def _flush(self):
        text = " ".join("".join(self.buf).split())
        self.buf.clear()
        if not text:
            return
        if self.sections and self.sections[-1][0] == self.title:
            self.sections[-1] = (self.title, f"{self.sections[-1][1]}\n{text}".strip())
        else:
            self.sections.append((self.title, text))

    def _finish_heading(self):
        name = " ".join("".join(self.heading_parts).split())
        name = re.sub(r"\[?编辑\]?", "", name).strip()
        self.heading_parts.clear()
        self.heading = None
        if name:
            self._flush()
            self.title = name

    # -- HTMLParser 回调 --
    def handle_starttag(self, tag, attrs):
        classes = (dict(attrs).get("class") or "").split()
        if tag == "div" and "mw-parser-output" in classes:
            self.inside = True
            return
        if not self.inside:
            return
        if self.skip:
            if tag not in _VOID:
                self.skip += 1
            return
        if tag in _SKIP:
            self.skip = 1
            return
        if tag in ("h1", "h2", "h3", "h4"):
            self._flush()
            self.heading = tag
            self.heading_parts = []
            return
        if tag in _BLOCKS:
            self._flush()
        elif tag == "br":
            self.buf.append("\n")

    def handle_endtag(self, tag):
        if not self.inside:
            return
        if self.skip:
            if tag not in _VOID:
                self.skip -= 1
            return
        if self.heading and tag == self.heading:
            self._finish_heading()
            return
        if tag in _BLOCKS:
            self._flush()

    def handle_data(self, data):
        if not self.inside or self.skip:
            return
        if self.heading:
            self.heading_parts.append(data)
        else:
            self.buf.append(data)

    def close(self):
        super().close()
        self._flush()


def sections_of(html: str) -> list[tuple[str, str]]:
    parser = _Sections()
    parser.feed(html)
    parser.close()
    out = []
    for title, text in parser.sections:
        title = title.strip() or "简介"
        # 维基导游的 listing 前面会带经纬度（两个小数连在一起时没有分隔符），
        # 对语义检索是纯噪声，按「行首的长数字串」剥掉
        text = re.sub(r"(?m)^[0-9.\u3000 ]{6,}", "", text)
        # listing 里的经纬度（页面用绝对定位的 geo span 输出，取文本时两个小数
        # 会粘在一起，如 30.66365104.053293）。价格不会带 4 位以上小数，可以整段剥掉。
        text = re.sub(r"\d{1,3}\.\d{4,}", "", text)
        text = re.sub(r"\.\d{4,}", "", text)
        text = re.sub(r"\[\d+\]", "", text)          # 脚注序号
        # 只压行内空白，保留换行：每个 listing 一行，切块时按行装箱更精准
        text = re.sub(r"[ \t\u3000]+", " ", text)
        text = re.sub(r"\n[ ]*\n+", "\n", text).strip()
        # 价格和日期**不在这里洗**。实测在入库时剥掉数字会把留出集
        # Recall@3 从 0.82 拉低到 0.75：数字在原文里占了不少语义分量，
        # 删掉后片段变短、切块边界移动，住宿类章节（「XX客栈 ¥120」一行一个）
        # 受影响最大。库里留着旧价格不影响用户——注入提示词前还有一道
        # itinerary._strip_stale 会把它们剥掉，模型看不到。
        if title in _DROP_SECTIONS or len(text) < 30:
            continue
        out.append((title, text))
    return out


def fetch(client: httpx.Client, city: str) -> tuple[int, int, str]:
    """返回 (pageid, revid, html)。429/5xx 退避重试，其余错误直接抛。"""
    params = {
        "action": "parse", "page": city, "prop": "text|revid", "format": "json",
        "formatversion": 2, "variant": "zh-hans", "disableeditsection": 1,
    }
    last = None
    for attempt in range(4):
        try:
            resp = client.get(ENDPOINT, params=params, headers={"User-Agent": AGENT})
            if resp.status_code == 429:
                time.sleep(4 * (attempt + 1))
                last = RuntimeError("HTTP 429（限流）")
                continue
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data.get("error"), dict):
                raise RuntimeError(str(data["error"].get("code") or "接口错误"))
            page = data["parse"]
            return int(page["pageid"]), int(page["revid"]), page["text"]
        except Exception as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{city} 抓取失败：{last}")


def main() -> int:
    init_db()
    rows: list[dict] = []
    failures: list[str] = []
    per_city: dict[str, int] = {}

    timeout = httpx.Timeout(30.0, connect=8.0)
    with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=True) as client:
        for index, city in enumerate(CITIES, 1):
            try:
                pageid, revid, html = fetch(client, city)
                sections = sections_of(html)
                city_rows = 0
                for title, text in sections:
                    for chunk_index, chunk in enumerate(knowledge.chunk_text(text, title=title)):
                        rows.append(
                            {
                                "source_type": "guide",
                                "source_id": pageid,
                                "chunk_index": 0,       # 占位，下面按城市内顺序重排
                                "city": city,
                                "title": f"{city}·{title}"[:200],
                                "content": chunk,
                                "tags": "",
                                "source_url": f"https://zh.wikivoyage.org/w/index.php?oldid={revid}",
                            }
                        )
                        city_rows += 1
                per_city[city] = city_rows
                print(f"[{index:02d}/{len(CITIES)}] {city}: {city_rows} 个片段")
            except Exception as exc:
                failures.append(f"{city}: {exc}")
                print(f"[{index:02d}/{len(CITIES)}] {city}: 跳过（{exc}）")
            time.sleep(1.2)  # Wikimedia 对匿名请求限速，串行 + 间隔最稳

    # chunk_index 在 (source_type, source_id) 内必须唯一，按城市内出现顺序编号
    counters: dict[int, int] = {}
    for row in rows:
        n = counters.get(row["source_id"], 0)
        row["chunk_index"] = n
        counters[row["source_id"]] = n + 1

    print(f"\n共 {len(per_city)} 个城市成功，{len(rows)} 个片段；失败 {len(failures)} 个")
    for line in failures:
        print("  失败：" + line)
    if not rows:
        print("没有任何片段，已中止，旧数据未改动")
        return 1

    print("开始向量化（可能需要 1-3 分钟）…")
    vectors = asyncio.run(knowledge.embed_texts([r["content"] for r in rows]))
    if len(vectors) != len(rows):
        print(f"向量数量不符（{len(vectors)}/{len(rows)}），已中止，旧数据未改动")
        return 1

    with SessionLocal() as db:
        db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.source_type == "guide"))
        db.add_all(
            [
                KnowledgeChunk(**row, embedding=np.asarray(vec, dtype=np.float32).tobytes())
                for row, vec in zip(rows, vectors)
            ]
        )
        db.commit()

    knowledge.clear_cache()
    print(f"完成：写入 {len(rows)} 个攻略片段")
    print("当前知识库：", knowledge.stats())
    print(f"正文授权：CC BY-SA 4.0 {LICENSE_URL}；出处链接已存入 source_url")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
