"""把 spots 表灌进知识库（Day 1 验证链路用）。

用法（在 backend/ 下）：
    python scripts/ingest_spots.py

幂等：先删 source_type='spot' 的旧片段再整批写入，重跑不会产生重复。
注意顺序：只有全部向量都成功拿到才动数据库；embedding 中途失败时旧数据原样保留。
"""
import asyncio
import sys
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from sqlalchemy import delete  # noqa: E402

from app.db.database import SessionLocal, init_db  # noqa: E402
from app.db.models import KnowledgeChunk, Spot  # noqa: E402
from app.services import knowledge  # noqa: E402


def compose(spot: Spot) -> str:
    """一条景点拼成一段可检索文本。

    线上库的 description 大量为空（高德 POI 只给了名称/分类），所以不能像
    设计稿那样只依赖 description——名称、区县、标签本身就是有效信息，
    拼进去至少能回答「成都有哪些景点 / 某区有什么」。
    """
    head = f"景点：{spot.name}"
    where = f"城市：{spot.city}" + (f"{spot.district}" if spot.district else "")
    parts = [head, where]
    if spot.tags:
        parts.append(f"标签：{spot.tags}")
    if spot.address:
        parts.append(f"地址：{spot.address}")
    if spot.description:
        parts.append(str(spot.description))
    return "｜".join(parts)


def main() -> int:
    init_db()
    with SessionLocal() as db:
        spots = db.query(Spot).filter(Spot.city != "").order_by(Spot.id).all()

    rows: list[dict] = []
    skipped = 0
    for spot in spots:
        text = compose(spot)
        if len(text) < 8:
            skipped += 1
            continue
        chunks = knowledge.chunk_text(text, title=spot.name)
        for index, chunk in enumerate(chunks):
            rows.append(
                {
                    "source_type": "spot",
                    "source_id": spot.id,
                    "chunk_index": index,
                    "city": spot.city or "",
                    "title": spot.name or "",
                    "content": chunk,
                    "tags": spot.tags or "",
                    "source_url": spot.source_url or "",
                }
            )

    if not rows:
        print(f"没有可入库的景点（读取 {len(spots)} 条，跳过 {skipped} 条）")
        return 1

    print(f"读取 {len(spots)} 条景点，生成 {len(rows)} 个片段，开始向量化…")
    vectors = asyncio.run(knowledge.embed_texts([r["content"] for r in rows]))

    if len(vectors) != len(rows):
        print(f"向量数量不符（{len(vectors)}/{len(rows)}），已中止，旧数据未改动")
        return 1

    with SessionLocal() as db:
        db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.source_type == "spot"))
        db.add_all(
            [
                KnowledgeChunk(**row, embedding=np.asarray(vec, dtype=np.float32).tobytes())
                for row, vec in zip(rows, vectors)
            ]
        )
        db.commit()

    knowledge.clear_cache()
    print(f"完成：处理 {len(spots) - skipped} 条景点，写入 {len(rows)} 个片段（跳过 {skipped} 条）")
    print("当前知识库：", knowledge.stats())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
