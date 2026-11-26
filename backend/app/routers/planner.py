"""规划接口（v0.7）：LangGraph 流式编排（SSE）+ 规划记录落库。

事件流（SSE）：
  event: log     节点过程日志（JSON: {message}）
  event: token   打字机文本块（JSON: {text}）—— 追问/行程说明逐字推送
  event: result  最终结构化结果（JSON: {done, question, collected, itinerary, verify_logs, weather, plan_id})
  event: done    流结束
"""
import asyncio
import json
import math
import time
from uuid import UUID

from fastapi import APIRouter, Depends, Response, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session
from sqlalchemy import update

from app.db.database import get_db
from app.db.models import AiPlan, User
from app.routers.auth import get_current_user_optional
from app.services import plan_jobs
from app.services.plan_jobs import clean_messages

router = APIRouter(prefix="/api/plan", tags=["plan"])


class StreamIn(BaseModel):
    history: list[dict] = Field(default_factory=list,max_length=200)
    user_input: str = Field(min_length=1, max_length=500)
    prev_collected: dict = Field(default_factory=dict)
    session_id: str = Field(default="", max_length=50)
    messages: list[dict] = Field(default_factory=list,max_length=200)  # 对话快照

    @model_validator(mode='after')
    def bound_payload(self):
        if len(self.model_dump_json().encode('utf-8')) > 256*1024:
            raise ValueError('规划输入过大，请缩短对话后再试。')
        return self


class SnapshotIn(BaseModel):
    """v1.1 历史快照更新：聊天内容与地图视角（中心/缩放）。"""
    messages: list[dict] | None = None
    map_state: dict | None = None
    version: int | None = Field(default=None,ge=1)
    revision: UUID | None = None


class VersionedPlanIn(BaseModel):
    version: int = Field(ge=1)
    revision: UUID


class ProtectedSnapshotIn(SnapshotIn):
    version: int = Field(ge=1)
    revision: UUID


class ReliablePlanIn(StreamIn):
    request_id: UUID
    guest_key: UUID | None = None


# 快照防膨胀上限在 services/plan_jobs.py 里统一定义（那边落库也要用同一套规则）


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/history")
def plan_history(
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_optional),
):
    """我的历史规划列表（登录用户）。"""
    response.headers['Cache-Control']='no-store'
    if user is None:
        return {"items": [], "note": "游客不保存历史，登录后自动记录"}
    rows = (
        db.query(AiPlan)
        .filter(AiPlan.user_id == user.id, AiPlan.status == "completed")
        .order_by(AiPlan.created_at.desc())
        .limit(20)
        .all()
    )
    items = []
    for p in rows:
        intent = json.loads(p.intent_json or "{}")
        result = json.loads(p.result_json or "{}")
        spots = sum(len(d.get("spots") or []) for d in result.get("days") or [])
        traffic = result.get("traffic") or {}
        items.append({
            "id": p.id,
            'version':p.version,'revision':p.revision,'status':p.status,
            "session_id": p.session_id,
            "city": intent.get("destination") or intent.get("city") or "未知城市",
            "days": len(result.get("days") or []),
            "title": p.title or result.get("title") or "",
            "summary": p.summary or result.get("summary") or "",
            "spots": spots,
            # v1.1 出行难度/交通可达性/住宿：历史列表直接可见，方便横向比较不同目的地
            "difficulty": traffic.get("difficulty_overall") or "",
            "travel_min": int(traffic.get("total_min") or 0),
            "traffic_cost": int(traffic.get("cost") or 0),
            "stay_range": _stay_range(result),
            "created_at": p.created_at.strftime("%Y-%m-%d %H:%M"),
        })
    return {"items": items}


# ============ v1.3 后台任务接口（前端现在走这组）============
# 顺序很重要：/jobs 系列必须注册在 /{plan_id} 之前，
# 否则 GET /api/plan/jobs 会先命中 /{plan_id}，被 int 转换挡下来变成 422。


@router.post("/jobs")
async def create_plan_job(
    body: StreamIn,
    response: Response,
    user: User | None = Depends(get_current_user_optional),
):
    """下单式生成：立刻返回 job_id，真正的生成在后台跑。

    和 /stream 的区别就一句话：这里的任务不属于任何 HTTP 连接。
    前端切页面、刷新、关标签页，任务都继续跑到底。
    """
    response.headers['Cache-Control'] = 'no-store'
    job = plan_jobs.create_job(
        user_id=(user.id if user else 0),
        payload=body.model_dump(),
    )
    return {"job_id": job.id, "status": job.status}


@router.get('/jobs/capabilities')
def job_capabilities(response:Response):
    response.headers['Cache-Control']='no-store'
    return {'reliable_creation':True,'scoped_guests':True,'durable':True}


@router.post('/jobs/create')
async def create_plan_once(body:ReliablePlanIn,response:Response,user:User|None=Depends(get_current_user_optional)):
    reply,created=plan_jobs.create_once(user_id=user.id if user else 0,
        payload=body.model_dump(exclude={'request_id','guest_key'}),request_id=body.request_id,guest_key=body.guest_key)
    response.headers['Cache-Control']='no-store';response.status_code=201 if created else 200
    return reply


@router.post('/jobs/create/cancel')
def cancel_plan_creation(body:ReliablePlanIn,response:Response,user:User|None=Depends(get_current_user_optional)):
    response.headers['Cache-Control']='no-store'
    return plan_jobs.cancel_creation(user_id=user.id if user else 0,
        payload=body.model_dump(exclude={'request_id','guest_key'}),request_id=body.request_id,guest_key=body.guest_key)


@router.get("/jobs/active")
def active_plan_job(response: Response, user: User | None = Depends(get_current_user_optional)):
    """找回"我最近一条还在跑的任务"。

    用于用户关掉标签页再打开：新标签的 sessionStorage 是空的，
    但后端任务还在跑，登录用户可以靠这条把它接回来。
    """
    response.headers['Cache-Control'] = 'no-store'
    job = plan_jobs.find_active(user.id if user else 0)
    if job is None:
        return {"job": None}
    return {"job": {**job.snapshot(0), "request": job.request}}


@router.get("/jobs/{job_id}")
def poll_plan_job(
    job_id: str,
    response: Response,
    since: int = 0,
    user: User | None = Depends(get_current_user_optional),
):
    """拉增量事件。since 传上次收到的最大 seq，只回比它新的事件。

    前端按固定节奏轮询（约 500ms 一次）。轮询而不是长连接，
    是因为轮询断了随手重试就行——不会像 SSE 那样一断就把任务带走。
    """
    from fastapi import HTTPException

    response.headers['Cache-Control'] = 'no-store'
    job = plan_jobs.get_job(job_id, user.id if user else 0)
    if job is None:
        raise HTTPException(404, "任务不存在或已过期")
    return job.snapshot(max(0, since))


@router.delete("/jobs/{job_id}")
def cancel_plan_job(
    job_id: str,
    user: User | None = Depends(get_current_user_optional),
):
    """取消任务。用户开新会话时顺手掐掉，别让旧任务白烧模型调用。"""
    from fastapi import HTTPException

    ok = plan_jobs.cancel_job(job_id, user.id if user else 0)
    if not ok:
        raise HTTPException(404, "任务不存在或已过期")
    return {"ok": True, "job_id": job_id}


@router.get("/{plan_id}")
def plan_detail(
    plan_id: int,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_optional),
):
    """单条历史规划详情（仅本人可见，已删除的打不开）。"""
    if user is None:
        from fastapi import HTTPException
        raise HTTPException(401, "登录后可查看历史规划")
    response.headers['Cache-Control']='no-store'
    p = _ensure_alive_plan(plan_id, db, user)
    return {
        "id": p.id,
        "status": p.status,
        'version':p.version,'revision':p.revision,
        "intent": json.loads(p.intent_json or "{}"),
        "verify_logs": json.loads(p.process_json or "[]"),
        "itinerary": json.loads(p.result_json or "{}"),
        "chat": json.loads(p.chat_json or "[]"),
        "map_state": json.loads(p.map_json or "{}"),
        "created_at": p.created_at.strftime("%Y-%m-%d %H:%M"),
    }


def _stay_range(result: dict) -> str:
    """从行程里取优先推荐住宿的价格区间（历史列表展示用）。"""
    stays = result.get("stays") or []
    pick_name = (result.get("stay_pick") or {}).get("name") or ""
    for s in stays:
        if isinstance(s, dict) and s.get("name") == pick_name:
            return str(s.get("price_range") or "")
    for s in stays:
        if isinstance(s, dict) and s.get("recommended"):
            return str(s.get("price_range") or "")
    return ""


def _ensure_own_plan(plan_id: int, db: Session, user) -> AiPlan:
    """取本人规划记录，不存在/不是自己的统一 404（不暴露他人记录存在性）。"""
    from fastapi import HTTPException

    p = db.get(AiPlan, plan_id)
    if p is None or p.user_id != user.id:
        raise HTTPException(404, "记录不存在")
    return p


def _ensure_alive_plan(plan_id: int, db: Session, user) -> AiPlan:
    """在 _ensure_own_plan 基础上再挡一道：已软删的记录一律 404。"""
    from fastapi import HTTPException

    p = _ensure_own_plan(plan_id, db, user)
    if p.status == "deleted":
        raise HTTPException(404, "记录不存在")
    return p


RESTORE_SECONDS=8


def _plan_state(p):
    seconds=max(0,math.ceil((p.restore_until or 0)-time.time())) if p.status=='deleted' else 0
    return {'ok':True,'id':p.id,'version':p.version,'revision':p.revision,'status':p.status,
            'restore_until':p.restore_until,'restore_seconds_remaining':seconds,'can_restore':seconds>0}


def _version(p,version,revision):
    if version is None or revision is None:raise HTTPException(428,'请更新客户端，携带原版本和记录标识。')
    if str(revision)!=p.revision:raise HTTPException(409,'记录身份已变化，请重新读取。')


def _claim_plan(db,p,version,revision,**values):
    changed=db.execute(update(AiPlan).where(AiPlan.id==p.id,AiPlan.user_id==p.user_id,
        AiPlan.version==version,AiPlan.revision==str(revision),AiPlan.status==p.status)
        .values(version=version+1,**values).execution_options(synchronize_session=False))
    if changed.rowcount!=1:db.rollback();raise HTTPException(409,'历史方案已更新，本次未覆盖，请先核对。')
    db.commit();db.expire_all()
    return _plan_state(db.get(AiPlan,p.id))


@router.get('/{plan_id}/state')
def plan_state(plan_id:int,response:Response,db:Session=Depends(get_db),user=Depends(get_current_user_optional)):
    if user is None:raise HTTPException(401,'登录后可核对历史方案')
    response.headers['Cache-Control']='no-store'
    return _plan_state(_ensure_own_plan(plan_id,db,user))


def _delete(plan_id,version,revision,db,user):
    if user is None:raise HTTPException(401,'登录后可删除历史方案')
    p=_ensure_own_plan(plan_id,db,user);_version(p,version,revision)
    if p.status=='deleted' and p.version==version+1:return _plan_state(p)
    if p.status!='completed' or p.version!=version:raise HTTPException(409,'历史方案已更新，请先核对再决定。')
    return _claim_plan(db,p,version,revision,status='deleted',restore_until=time.time()+RESTORE_SECONDS)


@router.delete('/{plan_id}/content')
def delete_plan_content(plan_id:int,response:Response,version:int=Query(ge=1),revision:UUID=Query(),
    db:Session=Depends(get_db),user=Depends(get_current_user_optional)):
    response.headers['Cache-Control']='no-store'
    return _delete(plan_id,version,revision,db,user)


@router.delete('/{plan_id}')
def delete_plan(plan_id:int,response:Response,version:int|None=Query(default=None,ge=1),revision:UUID|None=None,
    db:Session=Depends(get_db),user=Depends(get_current_user_optional)):
    response.headers['Cache-Control']='no-store'
    return _delete(plan_id,version,revision,db,user)


def _restore(plan_id,version,revision,db,user):
    if user is None:raise HTTPException(401,'登录后可恢复历史方案')
    p=_ensure_own_plan(plan_id,db,user);_version(p,version,revision)
    if p.status=='completed' and p.version==version+1:return _plan_state(p)
    if p.status!='deleted' or p.version!=version:raise HTTPException(409,'历史方案已更新，请先核对。')
    if not p.restore_until or p.restore_until<=time.time():raise HTTPException(410,'撤销期限已结束，本次未恢复。')
    changed=db.execute(update(AiPlan).where(AiPlan.id==p.id,AiPlan.user_id==user.id,
        AiPlan.revision==str(revision),AiPlan.version==version,AiPlan.status=='deleted',AiPlan.restore_until>time.time())
        .values(version=version+1,status='completed',restore_until=None).execution_options(synchronize_session=False))
    if changed.rowcount!=1:db.rollback();raise HTTPException(409,'恢复状态已变化，请先核对。')
    db.commit();db.expire_all()
    return _plan_state(db.get(AiPlan,plan_id))


@router.post('/{plan_id}/content/restore')
def restore_plan_content(plan_id:int,body:VersionedPlanIn,response:Response,db:Session=Depends(get_db),user=Depends(get_current_user_optional)):
    response.headers['Cache-Control']='no-store'
    return _restore(plan_id,body.version,body.revision,db,user)


@router.post('/{plan_id}/restore')
def restore_plan(plan_id:int,response:Response,body:VersionedPlanIn|None=None,
    db:Session=Depends(get_db),user=Depends(get_current_user_optional)):
    response.headers['Cache-Control']='no-store'
    return _restore(plan_id,body.version if body else None,body.revision if body else None,db,user)


def _snapshot(plan_id,body,db,user):
    if user is None:raise HTTPException(401,'登录后可保存快照')
    p=_ensure_alive_plan(plan_id,db,user);_version(p,body.version,body.revision)
    if p.version!=body.version or p.status!='completed':raise HTTPException(409,'快照已更新，请先核对，本次未覆盖。')
    values={}
    if body.messages is not None:values['chat_json']=json.dumps(clean_messages(body.messages)[-60:],ensure_ascii=False)
    if body.map_state is not None:
        center=body.map_state.get('center') or [];zoom=body.map_state.get('zoom')
        if center or zoom is not None:
            if not (isinstance(center,list) and len(center)==2 and all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) for x in center)
                and -180<=center[0]<=180 and -90<=center[1]<=90 and isinstance(zoom,(int,float)) and not isinstance(zoom,bool) and math.isfinite(zoom) and 1<=zoom<=20):
                raise HTTPException(422,'地图视角无效')
        values['map_json']=json.dumps({'center':center,'zoom':zoom},ensure_ascii=False)
    if not values:return _plan_state(p)
    return _claim_plan(db,p,body.version,body.revision,**values)


@router.patch('/{plan_id}/snapshot/edit')
def protected_snapshot(plan_id:int,body:ProtectedSnapshotIn,response:Response,
    db:Session=Depends(get_db),user=Depends(get_current_user_optional)):
    response.headers['Cache-Control']='no-store'
    return _snapshot(plan_id,body,db,user)


@router.patch('/{plan_id}/snapshot')
def update_snapshot(plan_id:int,body:SnapshotIn,response:Response,
    db:Session=Depends(get_db),user=Depends(get_current_user_optional)):
    response.headers['Cache-Control']='no-store'
    return _snapshot(plan_id,body,db,user)


@router.post("/stream")
async def plan_stream(
    body: StreamIn,
    user: User | None = Depends(get_current_user_optional),
):
    """SSE 兼容入口（外部集成 / 调试用；前端 v1.3 起改用 /jobs）。

    关键变化：底层也是后台任务，这个连接只是"收件人"。
    连接断了只会停止转发事件，不会取消生成——所以就算还有旧前端在用，
    切页面也不会白等一场，回到页面重新拉 /jobs 就能接上。
    """
    job = plan_jobs.create_job(
        user_id=(user.id if user else 0),
        payload=body.model_dump(),
    )

    async def event_stream():
        since = 0
        while True:
            current = plan_jobs.get_job(job.id,user.id if user else 0)
            if current is None: break
            for ev in current.snapshot(since)['events']:
                since = ev['seq']
                yield _sse(ev["type"], ev["data"])
            if current.status != "running":
                break
            await asyncio.sleep(0.05)
        yield _sse("done", {})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
