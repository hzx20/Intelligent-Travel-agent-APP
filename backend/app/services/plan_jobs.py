"""后台规划任务（v1.3）：让"正在生成"活过 HTTP 请求。

为什么要有这一层？
    在 v1.2 及以前，规划是直接跑在 SSE 响应生成器里的：

        return StreamingResponse(event_stream())   # 生成逻辑写在 event_stream 里

    这种写法有个致命前提——**请求必须一直连着**。用户一点别的页面、刷新一下、
    或者把标签页关掉，连接就断了，Starlette 会顺手取消这个响应生成器，
    里面的生成任务当场死亡。用户看到的现象就是"切走 = 白等"，
    只能事后去历史方案里捡那次跑到一半的结果。

    现在改成"下单 + 轮询"：
        POST   /api/plan/jobs        建任务，立刻返回 job_id（不阻塞、不挂连接）
        GET    /api/plan/jobs/{id}   取增量事件（?since=上次收到的最大 seq）
        GET    /api/plan/jobs/active 找回"我最近一条还在跑的任务"（刷新/重开页面用）
        DELETE /api/plan/jobs/{id}   主动取消（开新会话时顺手掐掉，别白烧 token）

    任务是 asyncio.create_task 起的，生命周期跟 HTTP 请求完全无关：
    切页面、刷新、关标签页都不影响它，回到页面时按 job_id 续上就能看到进度。

为什么事件要缓存在内存里？
    因为前端会"断线重连"。重连时把 since 之后的事件补给它，
    用户看到的就是连续的过程日志，而不是"回来只剩一个结果"。

已知限制（重要，别踩）：
    1. 任务存在进程内存，只适合单 worker。本项目 uvicorn 就是 --workers 1。
       哪天要扩到多 worker，得把 _jobs 换成 Redis 或落一张任务表，
       否则轮询可能打到另一个进程上，查不到这个 job_id。
    2. 进程重启任务就没了。这是内存方案的代价，可接受：
       真正要紧的行程结果在任务完成时已经落库（ai_plans），不会丢。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field

from app.db.database import SessionLocal
from app.db.models import AiPlan

logger = logging.getLogger("uvicorn.error")

# 完成的任务保留多久：用户关掉页面过一阵再回来，仍能取到结果。
JOB_TTL_SEC = 30 * 60

# 落库用的 session 工厂。抽成模块级变量，是为了测试能换成内存库；
# 生产环境它就是 SessionLocal。
SESSION_FACTORY = SessionLocal

# job_id -> PlanJob。单进程内存表，理由见模块开头"已知限制"。
_jobs: dict[str, "PlanJob"] = {}


@dataclass
class PlanJob:
    """一次生成任务的全部状态。线程/协程安全靠"同一事件循环内单线程"保证。"""

    id: str
    user_id: int                 # 0 = 游客（不落库，只看结果）
    session_id: str = ""
    status: str = "running"      # running | done | error | cancelled
    seq: int = 0                 # 事件序号（只增），前端靠它做增量拉取
    events: list[dict] = field(default_factory=list)
    text: str = ""               # token 累计文本（追问文案），便于重连时整体补齐
    result: dict | None = None   # 最终 result 事件的内容
    plan_id: int | None = None   # 落库后的记录 id（游客为 None）
    error: str = ""
    request: dict = field(default_factory=dict)  # 原始请求，重连时用来重建对话
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    task: asyncio.Task | None = None

    def emit(self, kind: str, data: dict) -> None:
        """追加一条事件。kind 与旧 SSE 的事件名保持一致：log / token / result。"""
        self.seq += 1
        self.events.append({"seq": self.seq, "type": kind, "data": data})
        if kind == "token":
            self.text += str(data.get("text") or "")
        self.updated_at = time.time()

    def snapshot(self, since: int = 0) -> dict:
        """给前端的一帧：since 之后的新事件 + 当前状态。

        只回增量是有意的：生成过程可能几十条日志，每次都全量回一遍没必要。
        """
        return {
            "job_id": self.id,
            "status": self.status,
            "seq": self.seq,
            "events": [e for e in self.events if e["seq"] > since],
            "text": self.text,
            "result": self.result,
            "plan_id": self.plan_id,
            "error": self.error,
        }


def _gc() -> None:
    """清掉过期的已完成任务。建新任务时顺手扫一遍，成本可以忽略。"""
    now = time.time()
    for jid, job in list(_jobs.items()):
        if job.status != "running" and now - job.updated_at > JOB_TTL_SEC:
            _jobs.pop(jid, None)


def create_job(*, user_id: int, payload: dict) -> PlanJob:
    """建任务并立刻开跑。调用方（路由）马上就能拿到 job_id 返回前端。

    必须在事件循环里调用（路由是 async，满足）。
    """
    _gc()
    job = PlanJob(
        id=uuid.uuid4().hex,
        user_id=user_id,
        session_id=str(payload.get("session_id") or ""),
        request=payload,
    )
    _jobs[job.id] = job
    # 不 await：让它在后台自己跑。task 引用存在 job 上，防止被垃圾回收。
    job.task = asyncio.create_task(run_job(job))
    return job


def get_job(job_id: str, user_id: int) -> PlanJob | None:
    """取任务，并校验归属：别人的 job_id 一律当作不存在。"""
    job = _jobs.get(job_id)
    if job is None or job.user_id != user_id:
        return None
    return job


def find_active(user_id: int) -> PlanJob | None:
    """找"我最近一条还在跑的任务"。

    用途：用户关掉标签页再打开（sessionStorage 已经没了），
    登录用户可以靠这条把它接回来，不至于让后端白跑一趟却看不到结果。

    游客（user_id=0）直接返回 None：游客没有身份，所有人共用 0 这个桶，
    真按它查会把别人的输入和行程当成"你的任务"顶上来——宁可不恢复。
    """
    if not user_id:
        return None
    running = [j for j in _jobs.values() if j.user_id == user_id and j.status == "running"]
    if not running:
        return None
    return max(running, key=lambda j: j.created_at)


def cancel_job(job_id: str, user_id: int) -> bool:
    """主动取消（开新会话时用）。取消不是错误，前端只提示一句"已取消"。"""
    job = get_job(job_id, user_id)
    if job is None:
        return False
    if job.task is not None and not job.task.done():
        job.task.cancel()
    return True


async def iter_updates(state: dict):
    """逐节点产出 (节点名, 节点更新, 本次新增日志)。

    单独抽成模块级函数有两个理由：
      1. 测试可以把它换成假的生成器，不花真模型的钱、也不依赖网络；
      2. 编排细节（LangGraph）和任务生命周期解耦，将来换框架只动这里。
    """
    from app.services.graph import plan_graph

    emitted = 0
    async for chunk in plan_graph.astream(state):
        for node_name, updates in chunk.items():
            # 节点 updates 里的 logs 是"从头累计"的全量，
            # 直接全发会让每条日志重复 N 遍，所以按已发数量切片
            logs = updates.get("logs") or []
            new_logs = logs[emitted:]
            emitted += len(new_logs)
            yield node_name, updates, new_logs


async def run_job(job: PlanJob) -> None:
    """任务主体：跑图 → 推事件 → 落库 → 收尾。

    这里刻意不碰任何 HTTP 对象：断开与否和它无关。
    """
    req = job.request
    state = {
        "history": req.get("history") or [],
        "user_input": req.get("user_input") or "",
        "prev_collected": req.get("prev_collected") or {},
        "logs": [],
    }
    final: dict = {}
    try:
        async for node_name, updates, new_logs in iter_updates(state):
            for log in new_logs:
                job.emit("log", {"node": node_name, "message": log})
            final.update(updates)
            # 澄清未完成：把追问文本按两字一块推出去，前端拼起来就是打字机效果
            if node_name == "clarify" and not updates.get("done"):
                question = str(updates.get("question") or "")
                for i in range(0, len(question), 2):
                    job.emit("token", {"text": question[i:i + 2]})
                    await asyncio.sleep(0.03)

        done = bool(final.get("done"))
        plan_id = None
        # 登录用户且确实生成了行程 → 落库。游客不落库（历史列表也只对登录用户开放）
        if job.user_id and done and final.get("itinerary"):
            plan_id = _save_plan(job, final)

        job.plan_id = plan_id
        job.result = {
            "done": done,
            "question": final.get("question") or "",
            "collected": final.get("collected") or {},
            "itinerary": final.get("itinerary") or {},
            "verify_logs": final.get("verify_logs") or [],
            "weather": final.get("weather") or {},
            "plan_id": plan_id,
            "saved": plan_id is not None,
        }
        job.emit("result", job.result)
        job.status = "done"
    except asyncio.CancelledError:
        # 用户主动取消（开了新会话 / 点了停止）：走正常收尾，不当异常往上抛
        job.status = "cancelled"
        job.emit("result", {"done": False, "cancelled": True})
        job.updated_at = time.time()
    except Exception:
        # 异常详情只进后端日志：str(e) 可能带出内部路径与库名
        logger.exception("后台规划任务失败 job=%s", job.id)
        job.error = "生成失败，请稍后重试"
        job.status = "error"
        job.emit("result", {"done": False, "error": job.error})
    finally:
        job.updated_at = time.time()
        job.task = None


def _save_plan(job: PlanJob, final: dict) -> int | None:
    """把行程写进 ai_plans，返回记录 id。

    注意：这里必须自己开 session。路由里那个 db 是请求作用域的，
    请求一结束就被关掉了，后台任务再拿它写库会炸。
    """
    itinerary = final.get("itinerary") or {}
    db = SESSION_FACTORY()
    try:
        record = AiPlan(
            user_id=job.user_id,
            session_id=job.session_id,
            status="completed",
            intent_json=json.dumps(final.get("collected") or {}, ensure_ascii=False),
            process_json=json.dumps(final.get("verify_logs") or [], ensure_ascii=False),
            result_json=json.dumps(itinerary, ensure_ascii=False),
            title=str(itinerary.get("title") or "")[:100],
            summary=str(itinerary.get("summary") or "")[:300],
            chat_json=json.dumps(clean_messages(job.request.get("messages")), ensure_ascii=False),
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return record.id
    except Exception:
        # 落库失败不该让用户丢掉这次生成结果：日志留痕，结果照常返回（saved=False）
        logger.exception("规划结果落库失败 job=%s", job.id)
        db.rollback()
        return None
    finally:
        db.close()


# 与 routers/planner.py 里的上限保持一致：防异常客户端把超大文本写进库
MAX_SNAPSHOT_MSGS = 200
MAX_MSG_TEXT = 4000


def clean_messages(msgs) -> list[dict]:
    """快照清洗：只留 {role, text}，条数和单条长度封顶。

    落库用的是清洗后的列表，而不是"来什么存什么"——
    messages 来自前端原样上传，不封顶的话异常客户端能借接口把超大文本写进 SQLite。
    正常前端的对话量（单轮几十字、全程几十条）完全不受影响。
    """
    out: list[dict] = []
    for m in (msgs or [])[:MAX_SNAPSHOT_MSGS]:
        if not isinstance(m, dict):
            continue
        out.append({
            "role": str(m.get("role") or "user")[:10],
            "text": str(m.get("text") or "")[:MAX_MSG_TEXT],
        })
    return out
