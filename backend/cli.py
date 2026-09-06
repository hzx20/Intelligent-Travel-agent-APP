"""命令行对话入口（Python 版）。

对应关系：server/cli.js（Node readline 循环）→ backend/cli.py（input 循环）
- Node 版 readline 关闭返回 null 会崩溃的坑，在 Python 里对应
  input() 遇到 EOF 抛 EOFError —— 这里直接捕获并优雅退出
运行方式（在项目根目录，用已装好依赖的虚拟环境）：
  C:\\Users\\i\\.workbuddy\\binaries\\python\\envs\\default\\Scripts\\python.exe backend\\cli.py
"""
import asyncio
import json
import sys
from pathlib import Path

# 保证 backend 包可导入
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.services.clarify import clarify_turn  # noqa: E402
from app.services.itinerary import generate_itinerary  # noqa: E402


def print_itinerary(it):
    print(f"\n📋 《{it.get('title', '未命名行程')}》 — {it.get('summary', '')}")
    for day in it.get("days", []):
        print(f"\n— Day {day.get('day')} · {day.get('theme', '')} —")
        for item in day.get("items", []):
            print(f"  {item.get('time', '')}  {item.get('activity', '')}（{item.get('poi', '')}）")
    budget = it.get("budget_summary", {})
    if budget:
        print(f"\n💰 预算汇总：{json.dumps(budget, ensure_ascii=False)}")


async def main():
    print("=== 智能旅行规划助手（FastAPI + LangChain 版）===")
    print("告诉我你想去哪儿玩，我来帮你规划。输入 /exit 退出。\n")
    history = []
    collected = {}

    while True:
        try:
            user_input = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break
        if not user_input or user_input == "/exit":
            print("再见！")
            break

        try:
            result = await clarify_turn(history, user_input, collected)
        except Exception as e:
            print(f"⚠️ 出错了：{e}")
            continue

        collected = result["collected"]
        history.append({"role": "user", "content": user_input})
        if result["question"]:
            history.append({"role": "assistant", "content": result["question"]})
            print(f"\n助手> {result['question']}")
            continue

        print("\n助手> 需求齐了，正在为你生成行程，请稍等……")
        try:
            it = await generate_itinerary(collected)
            print_itinerary(it)
        except Exception as e:
            print(f"⚠️ 行程生成失败：{e}")


if __name__ == "__main__":
    asyncio.run(main())
