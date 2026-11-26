"""把 export_knowledge.py 产出的 JSONL 导入当前数据库的知识库表。

幂等：先清空 knowledge_chunks 再整批写入；校验不过（条数、维度、base64）时
不动数据库。用于生产服务器（无法访问维基导游）接收本机灌好的语料。

用法（在 backend/ 下）：
    python scripts/import_knowledge.py --input ../knowledge-2026-11-26.jsonl
    python scripts/import_knowledge.py --input ... --dry-run
"""
import argparse
import base64
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from sqlalchemy import delete, insert  # noqa: E402

from app.db.database import SessionLocal, init_db  # noqa: E402
from app.db.models import KnowledgeChunk, now_local  # noqa: E402
from app.services.knowledge import EMBED_DIM  # noqa: E402

REQUIRED = ("source_type", "source_id", "chunk_index", "city", "title", "content", "embedding")


def load(path: Path) -> tuple[dict, list[dict]]:
    with path.open("r", encoding="utf-8") as handle:
        first = handle.readline()
        if not first:
            raise ValueError("文件为空")
        meta = json.loads(first)
        if not meta.get("_meta") or meta.get("version") != 1:
            raise ValueError("缺少元信息或版本不支持")
        rows = []
        for number, line in enumerate(handle, 2):
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            missing = [key for key in REQUIRED if key not in item]
            if missing:
                raise ValueError(f"第 {number} 行缺字段：{missing}")
            raw = base64.b64decode(item["embedding"], validate=True)
            if len(raw) != EMBED_DIM * 4:
                raise ValueError(f"第 {number} 行向量长度不符（{len(raw)} 字节）")
            rows.append({**item, "embedding": raw})
    if meta.get("count") != len(rows):
        raise ValueError(f"条数不符：元信息 {meta.get('count')}，实际 {len(rows)}")
    return meta, rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    path = Path(args.input).absolute()
    meta, rows = load(path)
    print(f"校验通过：{len(rows)} 个片段，维度 {meta.get('dim')}")
    if args.dry_run:
        return 0

    init_db()
    with SessionLocal() as db:
        db.execute(delete(KnowledgeChunk))
        db.execute(
            insert(KnowledgeChunk),
            [
                {
                    "source_type": row["source_type"],
                    "source_id": int(row["source_id"]),
                    "chunk_index": int(row["chunk_index"]),
                    "city": row["city"] or "",
                    "title": row["title"] or "",
                    "content": row["content"] or "",
                    "tags": row.get("tags") or "",
                    "embedding": row["embedding"],
                    "source_url": row["source_url"] or "",
                    "updated_at": now_local(),
                }
                for row in rows
            ],
        )
        db.commit()
        total = db.query(KnowledgeChunk).count()
    print(f"导入完成：knowledge_chunks 现有 {total} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
