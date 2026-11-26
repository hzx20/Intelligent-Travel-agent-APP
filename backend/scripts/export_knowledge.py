"""把知识库片段导出成可搬运的 JSONL（含 base64 向量）。

用途：生产服务器访问不了 zh.wikivoyage.org（实测超时），不能在上面直接跑
`ingest_guides.py`。改在本机灌好、导出、上传、导入，服务器不需要外网也能拿到语料。

用法（在 backend/ 下）：
    python scripts/export_knowledge.py --output ../knowledge-2026-11-26.jsonl

行格式：首行是元信息，其后每行一个片段；embedding 为 float32 原始字节的 base64。
"""
import argparse
import base64
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from sqlalchemy import text as sql_text  # noqa: E402

from app.db.database import engine  # noqa: E402
from app.services.knowledge import EMBED_DIM  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--force", action="store_true", help="允许覆盖已存在的文件")
    args = parser.parse_args()

    output = Path(args.output).absolute()
    if output.exists() and not args.force:
        print(f"目标已存在，未覆盖：{output}（确需覆盖加 --force）")
        return 1

    with engine.connect() as conn:
        rows = conn.execute(
            sql_text(
                "SELECT source_type, source_id, chunk_index, city, title, content, tags, "
                "embedding, source_url FROM knowledge_chunks ORDER BY id"
            )
        ).fetchall()

    if not rows:
        print("知识库为空，没有可导出的内容")
        return 1

    expected_bytes = EMBED_DIM * 4
    bad = [row.source_id for row in rows if not row.embedding or len(row.embedding) != expected_bytes]
    if bad:
        print(f"有 {len(bad)} 条向量缺失或维度不符，已中止（先重跑灌库脚本）")
        return 1

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        meta = {
            "_meta": True,
            "version": 1,
            "dim": EMBED_DIM,
            "count": len(rows),
        }
        handle.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for row in rows:
            handle.write(
                json.dumps(
                    {
                        "source_type": row.source_type,
                        "source_id": row.source_id,
                        "chunk_index": row.chunk_index,
                        "city": row.city,
                        "title": row.title,
                        "content": row.content,
                        "tags": row.tags,
                        "embedding": base64.b64encode(row.embedding).decode("ascii"),
                        "source_url": row.source_url,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )

    size = output.stat().st_size / 1024 / 1024
    print(f"已导出 {len(rows)} 个片段 → {output}（{size:.1f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
