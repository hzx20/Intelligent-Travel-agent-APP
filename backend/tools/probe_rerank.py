"""一次性探针：验证智谱托管 rerank 接口的真实可用性与参数边界。

查证结论（2026-11-27，docs.bigmodel.cn「文本重排序」）：端点存在。
本脚本实测三件事，全部以真实响应为准，不臆断：
1. 端点通不通、响应形状是否与官方文档一致（results[].index/relevance_score + usage）；
2. 上下文限制：10 条 × 400 字符候选（约等于线上 rerank_pool 默认值的工作负载）能不能过；
3. 一次调用的耗时与 token 用量（评估检索延迟增量）。

用法：python tools/probe_rerank.py   （在 backend/ 下，需要 .env 里有 ZHIPU_API_KEY）
"""
import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.llm import ZHIPU_BASE_URL  # noqa: E402

URL = f"{ZHIPU_BASE_URL}/rerank"

QUERY = "成都哪家火锅好吃"

# 400 字符的候选（chunk_text 的片段上限就是 400，模拟真实负载）
DOC_TEMPLATE = (
    "成都火锅推荐。成都的火锅以牛油锅底闻名，{i}号店位于市中心，"
    "锅底麻辣鲜香，毛肚、鹅肠、嫩牛肉是招牌菜，人均消费适中，"
    "高峰期需要排队等位，建议提前取号。店铺环境干净整洁，服务态度好，"
    "适合朋友聚餐和家庭聚会，周边交通便利，地铁直达。"
) * 12  # 重复铺满到约 400 字符


def probe(label: str, documents: list[str]) -> None:
    payload = {
        "model": settings.rerank_model,
        "query": QUERY,
        "documents": documents,
        "top_n": len(documents),
    }
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=15.0, trust_env=False) as client:
            resp = client.post(
                URL,
                headers={"Authorization": f"Bearer {settings.zhipu_api_key}"},
                json=payload,
            )
        elapsed = (time.perf_counter() - started) * 1000
        print(f"[{label}] HTTP {resp.status_code}，耗时 {elapsed:.0f} ms")
        body = resp.json()
        if resp.status_code == 200:
            results = body.get("results", [])
            usage = body.get("usage", {})
            print(f"  返回 {len(results)} 条，usage={json.dumps(usage, ensure_ascii=False)}")
            for item in results[:3]:
                print(
                    f"  - index={item.get('index')} score={item.get('relevance_score')}"
                    f" doc_head={str(documents[item.get('index', 0)])[:24]}"
                )
        else:
            print(f"  错误响应：{json.dumps(body, ensure_ascii=False)[:300]}")
    except Exception as exc:
        print(f"[{label}] 异常：{type(exc).__name__}: {exc}")


def main() -> int:
    if not settings.zhipu_api_key:
        print("未配置 ZHIPU_API_KEY，无法探测")
        return 1
    print(f"模型名：{settings.rerank_model}，端点：{URL}")

    # 1. 最小请求：2 条短文档
    probe("小请求(2条x40字)", ["成都火锅很有名，蜀九香是老牌连锁。", "成都的冬天比较湿冷，注意保暖。"])

    # 2. 线上默认负载：10 条 × ~400 字符
    docs = [DOC_TEMPLATE.format(i=i) for i in range(10)]
    print(f"文档长度样本：{len(docs[0])} 字符")
    probe("默认负载(10条x400字)", docs)

    # 3. 上限试探：20 条 × ~400 字符（若 4K 是总上下文，这里应该会报错）
    docs20 = [DOC_TEMPLATE.format(i=i) for i in range(20)]
    probe("上限试探(20条x400字)", docs20)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
