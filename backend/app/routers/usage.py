"""用量汇总接口（2026-11-27 新增）：LLM token 计量 + 检索缓存命中率。

挂在管理员守卫后面：计量数据涉及成本视角，只对管理员开放。
产出数字（交接文档第 4 节第 3 项）：日均调用量 / token 总量 / 缓存命中率。
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.llm_usage import summary as usage_summary
from app.db.database import get_db
from app.routers.admin import get_current_admin

router = APIRouter(
    prefix="/api/usage",
    tags=["usage"],
    dependencies=[Depends(get_current_admin)],  # 整组管理员守卫
)


@router.get("/summary")
def usage_summary_endpoint(
    days: int = Query(default=7, ge=1, le=90, description="统计窗口（天）"),
    db: Session = Depends(get_db),
):
    """按天聚合 LLM 调用量与 token 用量，附带检索缓存命中率。

    返回结构：
    {
      "days": 7,                    # 请求的窗口
      "per_day": [ ... ],           # 有数据的天，按日倒序
      "totals": { calls, prompt_tokens, completion_tokens, total_tokens, ok_calls },
      "avg_calls_per_day": 12.5,    # 日均调用量（按窗口天数平均）
      "retrieval_cache": { hits, misses, hit_rate }
    }
    """
    return usage_summary(db, days)
