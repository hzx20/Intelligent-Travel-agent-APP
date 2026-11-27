"""检索评测：算 Recall@3（前 3 条里是否出现该查询的正确答案）。

用法（在 backend/ 下）：
    python scripts/eval_retrieval.py                  # 开发集 20 条
    python scripts/eval_retrieval.py --set holdout    # 留出集 60 条
    python scripts/eval_retrieval.py --k 5 --min-score 0.30
    python scripts/eval_retrieval.py --sweep          # 三档阈值 × 三档 K 全跑一遍

两套评测集必须分开看：
- dev（eval_queries.json）：写同义词表、调权重时对着它改过，分数偏高，
  只能用来防回归，不能当成效果证明。
- holdout（eval_queries_holdout.json）：写完就没再按它调过参数，反映的是
  「换一批问法还能不能召回」。真实用户日志攒够之前，这是能拿到的最接近
  无偏的估计。

评测集的判定方式：每条查询带 expect_any（若干个必须能在片段里看到的关键词）。
为什么不用 knowledge_chunks.id 标注：灌库是整表重建，id 每次都会变，
用 id 标注的评测集重灌一次就全错。关键词判定可重复、可人工复核，也更接近
「用户能不能看到有用的内容」这个真实问题。
"""
import argparse
import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.services import knowledge  # noqa: E402

EVAL_DIR = BACKEND / "tests"
EVAL_SETS = {
    "dev": "eval_queries.json",
    "holdout": "eval_queries_holdout.json",
}


def load_queries(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit("评测集必须是 JSON 数组")
    return data


def hit(item: dict, results: list[dict]) -> bool:
    """前 k 条里任意一条覆盖了期望关键词，就算命中。"""
    tokens = [str(t) for t in (item.get("expect_any") or []) if str(t).strip()]
    if not tokens:
        return bool(results)  # 没标注的查询只要求"有结果"
    for ref in results:
        text = f"{ref.get('title') or ''} {ref.get('content') or ''}"
        if any(token in text for token in tokens):
            return True
    return False


def run(queries: list[dict], k: int, min_score: float, *, verbose: bool = True) -> dict:
    knowledge.clear_cache()  # 实测耗时要包含真实 embedding 调用
    hits = 0
    misses = []
    elapsed = []
    for item in queries:
        started = time.perf_counter()
        results = knowledge.search(
            item.get("query", ""), item.get("city", ""), k=k, min_score=min_score
        )
        elapsed.append((time.perf_counter() - started) * 1000)
        ok = hit(item, results)
        hits += 1 if ok else 0
        if not ok:
            misses.append(item.get("query", ""))
            if verbose:
                print(f"  未命中：{item.get('query')} @ {item.get('city','')}")
                for ref in results:
                    print(f"    - {ref['score']:.3f} {ref['title']} | {ref['content'][:60]}")
    total = len(queries)
    recall = hits / total if total else 0.0
    avg = sum(elapsed) / len(elapsed) if elapsed else 0.0
    return {"total": total, "hits": hits, "recall": recall, "misses": misses,
            "avg_ms": avg, "max_ms": max(elapsed) if elapsed else 0.0}


def report(result: dict, k: int) -> None:
    print(
        f"总查询 {result['total']}，命中 {result['hits']}，"
        f"Recall@{k} = {result['recall']:.2f}"
    )
    if result["misses"]:
        print("未命中的查询：" + "；".join(result["misses"]))
    print(f"平均检索耗时：{result['avg_ms']:.0f} ms（最慢 {result['max_ms']:.0f} ms）")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--min-score", type=float, default=0.35)
    parser.add_argument("--set", dest="eval_set", choices=sorted(EVAL_SETS), default="dev",
                        help="dev=调参用过（偏乐观）；holdout=写完未调参（更接近真实）")
    parser.add_argument("--sweep", action="store_true", help="阈值 0.30/0.35/0.40 × K 3/5/8")
    args = parser.parse_args()

    queries = load_queries(EVAL_DIR / EVAL_SETS[args.eval_set])
    print(f"评测集：{args.eval_set}（{len(queries)} 条）")
    if args.sweep:
        print("阈值\tK\tRecall\t平均耗时")
        for score in (0.30, 0.35, 0.40):
            for k in (3, 5, 8):
                r = run(queries, k, score, verbose=False)
                print(f"{score:.2f}\t{k}\t{r['recall']:.2f}\t{r['avg_ms']:.0f} ms")
        return 0

    result = run(queries, args.k, args.min_score)
    report(result, args.k)
    return 0 if result["recall"] >= 0.75 else 1


if __name__ == "__main__":
    raise SystemExit(main())
