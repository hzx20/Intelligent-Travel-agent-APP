"""Durable original /plan jobs with expiring exclusive execution leases.

HTTP requests create/read/cancel database rows. Conditional claims and fenced
writes select one executor across workers. A lost process may be retried once
after its lease expires. History and final state commit together.
Guest IDs are unlisted opaque capabilities; guests have no active lookup.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from app.db.database import SessionLocal
from app.db.models import AiPlan, PlanGenerationJob, PlanCreationRequest, User

logger = logging.getLogger('uvicorn.error')
SESSION_FACTORY = SessionLocal
JOB_TTL_SEC = 30 * 60
LEASE_SECONDS = 60
MAX_ATTEMPTS = 2
MAX_LOCAL_WORKERS = 4
MAX_SNAPSHOT_MSGS = 200
MAX_MSG_TEXT = 4000
_jobs: dict[str, 'PlanJob'] = {}  # Execution handles only; never a read source.
_scheduler = None
_stopping = False


def _json(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'))


class LostLease(Exception):
    pass


@dataclass
class PlanJob:
    id: str
    user_id: int
    session_id: str = ''
    status: str = 'running'
    seq: int = 0
    events: list[dict] = field(default_factory=list)
    text: str = ''
    result: dict | None = None
    plan_id: int | None = None
    error: str = ''
    request: dict = field(default_factory=dict)
    created_at: float = 0
    updated_at: float = 0
    attempts: int = 0
    lease_token: str = ''
    task: asyncio.Task | None = None

    def emit(self, kind, data):
        events = [*self.events,{'seq':self.seq+1,'type':kind,'data':data}]
        text = '' if kind == 'reset' else self.text
        if kind == 'token': text += str(data.get('text') or '')
        with SESSION_FACTORY() as db:
            claimed = db.execute(update(PlanGenerationJob).where(*_fence(self))
                .values(seq=self.seq+1,events_json=_json(events),text=text,updated_at=time.time()))
            if claimed.rowcount != 1: raise LostLease()
            db.commit()
        self.seq += 1; self.events = events; self.text = text

    def snapshot(self, since=0):
        return {'job_id':self.id,'status':self.status,'seq':self.seq,
                'events':[e for e in self.events if e['seq'] > since],
                'text':self.text,'result':self.result,'plan_id':self.plan_id,'error':self.error}


def _loaded(row):
    return PlanJob(id=row.id,user_id=row.user_id or 0,session_id=row.session_id,
                   status=row.status,seq=row.seq,events=json.loads(row.events_json),text=row.text,
                   result=json.loads(row.result_json) if row.result_json else None,
                   plan_id=row.plan_id,error=row.error,request=json.loads(row.request_json),
                   created_at=row.created_at,updated_at=row.updated_at,attempts=row.attempts,
                   lease_token=row.lease_token)


def _fence(job):
    return (PlanGenerationJob.id == job.id,PlanGenerationJob.status == 'running',
            PlanGenerationJob.lease_token == job.lease_token,PlanGenerationJob.lease_until > time.time())


def _new_row(user_id,payload):
    now = time.time()
    return PlanGenerationJob(id=uuid.uuid4().hex,user_id=user_id or None,
        session_id=str(payload.get('session_id') or ''),request_json=_json(payload),created_at=now,updated_at=now)


def _kick(job):
    try: _start_job(job.id)
    except Exception: logger.exception('Planning task committed; awaiting scheduler job=%s',job.id)


def create_job(*, user_id, payload):
    payload = {**payload,'messages':clean_messages(payload.get('messages'))}
    with SESSION_FACTORY() as db:
        row = _new_row(user_id,payload)
        db.add(row); db.commit(); job = _loaded(row)
    _kick(job)
    return job


def _creation_context(user_id,payload,request_id,guest_key):
    if not user_id and not guest_key: raise HTTPException(422,'游客创建需携带本次提交凭证。')
    if user_id and guest_key: raise HTTPException(422,'账号提交不能携带游客凭证。')
    owner = f'user:{user_id}' if user_id else 'guest:'+hashlib.sha256(str(guest_key).encode()).hexdigest()
    payload = {**payload,'messages':clean_messages(payload.get('messages'))}
    digest = hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return owner,str(request_id),payload,digest


def _check_receipt(receipt,digest):
    if receipt.payload_hash!=digest: raise HTTPException(409,'本次规划内容与原提交不一致，请先核对。')


def _creation_out(row,request_id,user_id,status=None):
    return {'job_id':row.id if row else None,'status':status or row.status,
            'request_id':request_id,'owner_id':user_id}


def create_once(*,user_id,payload,request_id,guest_key=None):
    owner,rid,payload,digest = _creation_context(user_id,payload,request_id,guest_key)
    for _ in range(3):
        with SESSION_FACTORY() as db:
            receipt = db.query(PlanCreationRequest).filter_by(owner_key=owner,request_id=rid).first()
            if receipt:
                _check_receipt(receipt,digest)
                row = db.get(PlanGenerationJob,receipt.job_id) if receipt.job_id else None
                if row is None or row.status!='running' and row.updated_at<time.time()-JOB_TTL_SEC:
                    raise HTTPException(410,'本次规划已取消或过期，不会重新创建。请核对历史方案。')
                return _creation_out(row,rid,user_id),False
            row = _new_row(user_id,payload)
            db.add(row);db.flush()
            db.add(PlanCreationRequest(owner_key=owner,user_id=user_id or None,request_id=rid,payload_hash=digest,job_id=row.id))
            try: db.commit(); job=_loaded(row)
            except IntegrityError: db.rollback();continue
        _kick(job)
        return _creation_out(row,rid,user_id),True
    raise HTTPException(409,'本次提交正在确认，请继续使用原标识重试。')


def cancel_creation(*,user_id,payload,request_id,guest_key=None):
    """Reserve cancellation even when the original create has not arrived yet."""
    owner,rid,payload,digest = _creation_context(user_id,payload,request_id,guest_key)
    for _ in range(8):
        with SESSION_FACTORY() as db:
            receipt = db.query(PlanCreationRequest).filter_by(owner_key=owner,request_id=rid).first()
            if receipt is None:
                db.add(PlanCreationRequest(owner_key=owner,user_id=user_id or None,request_id=rid,payload_hash=digest,cancelled=True))
                try: db.commit();return _creation_out(None,rid,user_id,'cancelled')
                except IntegrityError: db.rollback();continue
            _check_receipt(receipt,digest)
            row = db.get(PlanGenerationJob,receipt.job_id) if receipt.job_id else None
            if row is None or row.status!='running' and row.updated_at<time.time()-JOB_TTL_SEC:
                return _creation_out(None,rid,user_id,'cancelled' if receipt.cancelled else 'expired')
            if row.status!='running': return _creation_out(row,rid,user_id)
            result = {'done':False,'cancelled':True}
            events = [*json.loads(row.events_json),{'seq':row.seq+1,'type':'result','data':result}]
            changed = db.execute(update(PlanGenerationJob).where(PlanGenerationJob.id==row.id,
                PlanGenerationJob.status=='running',PlanGenerationJob.seq==row.seq)
                .values(status='cancelled',seq=row.seq+1,events_json=_json(events),result_json=_json(result),
                        lease_token='',lease_until=0,updated_at=time.time()))
            if changed.rowcount!=1:db.rollback();continue
            receipt.cancelled=True
            db.commit();reply=_creation_out(row,rid,user_id,'cancelled');jid=row.id
        _cancel_local(jid)
        return reply
    raise HTTPException(409,'停止状态正在变化，请继续核对本次提交。')


def _cancel_local(job_id):
    runtime = _jobs.get(job_id)
    if runtime and runtime.task:
        task=runtime.task;task.get_loop().call_soon_threadsafe(task.cancel)


def get_job(job_id, user_id):
    with SESSION_FACTORY() as db:
        row = db.get(PlanGenerationJob,job_id)
        if row is None or (row.user_id or 0) != user_id: return None
        if row.status != 'running' and row.updated_at < time.time()-JOB_TTL_SEC: return None
        return _loaded(row)


def find_active(user_id):
    if not user_id: return None
    with SESSION_FACTORY() as db:
        row = db.query(PlanGenerationJob).filter_by(user_id=user_id,status='running').order_by(PlanGenerationJob.created_at.desc()).first()
        return _loaded(row) if row else None


def cancel_job(job_id,user_id):
    for _ in range(8):
        with SESSION_FACTORY() as db:
            row = db.get(PlanGenerationJob,job_id)
            if row is None or (row.user_id or 0) != user_id: return False
            if row.status != 'running': return True
            job = _loaded(row)
            result = {'done':False,'cancelled':True}
            events = [*job.events,{'seq':job.seq+1,'type':'result','data':result}]
            changed = db.execute(update(PlanGenerationJob).where(PlanGenerationJob.id==job_id,
                PlanGenerationJob.status=='running',PlanGenerationJob.seq==job.seq)
                .values(status='cancelled',seq=job.seq+1,events_json=_json(events),result_json=_json(result),
                        lease_token='',lease_until=0,updated_at=time.time()))
            if changed.rowcount != 1: db.rollback(); continue
            db.commit()
        _cancel_local(job_id)
        return True
    from fastapi import HTTPException
    raise HTTPException(409,'任务状态正在变化，请重试取消。')


def _claim(job_id):
    now = time.time(); token = uuid.uuid4().hex
    with SESSION_FACTORY() as db:
        claimed = db.execute(update(PlanGenerationJob).where(PlanGenerationJob.id==job_id,
            PlanGenerationJob.status=='running',PlanGenerationJob.lease_until<=now,
            PlanGenerationJob.attempts<MAX_ATTEMPTS).values(lease_token=token,lease_until=now+LEASE_SECONDS,
                attempts=PlanGenerationJob.attempts+1,updated_at=now))
        if claimed.rowcount != 1: db.rollback(); return None
        db.commit(); job = _loaded(db.get(PlanGenerationJob,job_id))
    if job.attempts > 1: job.emit('reset',{'message':'服务重新连接，正在恢复本次生成。'})
    return job


def _start_job(job_id):
    if _stopping or job_id in _jobs or len(_jobs)>=MAX_LOCAL_WORKERS: return
    job = _claim(job_id)
    if job is None: return
    _jobs[job_id] = job
    job.task = asyncio.create_task(run_job(job))
    def finished(task):
        if _jobs.get(job_id) is job: _jobs.pop(job_id,None)
        if not task.cancelled() and task.exception(): logger.error('Planning worker failed: %s',type(task.exception()).__name__)
    job.task.add_done_callback(finished)


async def _heartbeat(job):
    while True:
        await asyncio.sleep(max(0.1,LEASE_SECONDS/3))
        try:
            with SESSION_FACTORY() as db:
                alive = db.execute(update(PlanGenerationJob).where(*_fence(job))
                    .values(lease_until=time.time()+LEASE_SECONDS))
                db.commit()
            if alive.rowcount == 1: continue
        except Exception:
            logger.exception('Planning lease renewal failed job=%s',job.id)
        if job.task: job.task.cancel()
        return


def _finish(job, result, status='done', final=None):
    """Fence and save history/result in one transaction; cancellation wins CAS."""
    with SESSION_FACTORY() as db:
        claimed = db.execute(update(PlanGenerationJob).where(*_fence(job)).values(status='finishing'))
        if claimed.rowcount != 1: raise LostLease()
        plan_id = None;plan_version=None;plan_revision=None
        if final and job.user_id and result.get('done') and result.get('itinerary'):
            user = db.get(User,job.user_id)
            if user is None or not user.is_active:
                result = {'done':False,'error':'账号当前不可用，本次生成已停止。'}
                status = 'error'
            else:
                itinerary = result['itinerary']
                record = AiPlan(user_id=job.user_id,session_id=job.session_id,status='completed',
                    intent_json=_json(final.get('collected') or {}),process_json=_json(final.get('verify_logs') or []),
                    result_json=_json(itinerary),title=str(itinerary.get('title') or '')[:100],
                    summary=str(itinerary.get('summary') or '')[:300],chat_json=_json(clean_messages(job.request.get('messages'))))
                try:
                    with db.begin_nested():
                        db.add(record); db.flush(); plan_id = record.id;plan_version=record.version;plan_revision=record.revision
                except Exception:
                    # Preserve the generated result even when history cannot
                    # be saved; the task completion itself still must commit.
                    logger.exception('规划历史落库失败 job=%s',job.id)
                    plan_id = None
        result = {**result,'plan_id':plan_id,'saved':plan_id is not None,'plan_version':plan_version,'plan_revision':plan_revision}
        events = [*job.events,{'seq':job.seq+1,'type':'result','data':result}]
        db.execute(update(PlanGenerationJob).where(PlanGenerationJob.id==job.id)
            .values(status=status,seq=job.seq+1,events_json=_json(events),result_json=_json(result),
                    plan_id=plan_id,error=result.get('error',''),lease_token='',lease_until=0,updated_at=time.time()))
        db.commit()


async def iter_updates(state):
    from app.services.graph import plan_graph
    emitted = 0
    async for chunk in plan_graph.astream(state):
        for node_name,updates in chunk.items():
            logs = updates.get('logs') or []
            new_logs = logs[emitted:]; emitted += len(new_logs)
            yield node_name,updates,new_logs


async def run_job(job):
    heartbeat = asyncio.create_task(_heartbeat(job))
    req = job.request
    state = {'history':req.get('history') or [],'user_input':req.get('user_input') or '',
             'prev_collected':req.get('prev_collected') or {},'logs':[]}
    final = {}
    try:
        if job.user_id:
            with SESSION_FACTORY() as db:
                owner = db.get(User,job.user_id)
                allowed = owner is not None and owner.is_active
            if not allowed:
                _finish(job,{'done':False,'error':'账号当前不可用，本次生成已停止。'},status='error')
                return
        async with asyncio.timeout(10*60):
            async for node_name,updates,new_logs in iter_updates(state):
                for log in new_logs: job.emit('log',{'node':node_name,'message':str(log)[:4000]})
                final.update(updates)
                if node_name=='clarify' and not updates.get('done'):
                    question = str(updates.get('question') or '')[:4000]
                    for i in range(0,len(question),16):
                        job.emit('token',{'text':question[i:i+16]}); await asyncio.sleep(0.03)
        result = {'done':bool(final.get('done')),'question':final.get('question') or '',
                  'collected':final.get('collected') or {},'itinerary':final.get('itinerary') or {},
                  'verify_logs':final.get('verify_logs') or [],'weather':final.get('weather') or {}}
        _finish(job,result,final=final)
    except (asyncio.CancelledError,LostLease):
        # Shutdown leaves the lease to expire; explicit cancellation is already
        # committed. Neither may overwrite cancellation or a newer executor.
        pass
    except Exception:
        logger.exception('后台规划任务失败 job=%s',job.id)
        from app.core.config import settings
        error = '未配置 AI 密钥，请联系站点管理员' if not settings.zhipu_api_key else '生成失败，请稍后重试'
        try: _finish(job,{'done':False,'error':error},status='error')
        except LostLease: pass
    finally:
        heartbeat.cancel(); await asyncio.gather(heartbeat,return_exceptions=True)
        job.task = None


def _maintenance():
    now = time.time()
    with SESSION_FACTORY() as db:
        expired=db.query(PlanGenerationJob.id).filter(PlanGenerationJob.status!='running',PlanGenerationJob.updated_at<now-JOB_TTL_SEC)
        db.execute(update(PlanCreationRequest).where(PlanCreationRequest.job_id.in_(expired)).values(job_id=None))
        db.execute(delete(PlanGenerationJob).where(PlanGenerationJob.status!='running',PlanGenerationJob.updated_at<now-JOB_TTL_SEC))
        exhausted = db.query(PlanGenerationJob).filter(PlanGenerationJob.status=='running',
            PlanGenerationJob.lease_until<=now,PlanGenerationJob.attempts>=MAX_ATTEMPTS).all()
        for row in exhausted:
            error = '服务多次中断，本次生成已停止，请重新开始。'
            result = {'done':False,'error':error}
            events = [*json.loads(row.events_json),{'seq':row.seq+1,'type':'result','data':result}]
            db.execute(update(PlanGenerationJob).where(PlanGenerationJob.id==row.id,
                PlanGenerationJob.status=='running',PlanGenerationJob.lease_until<=now,PlanGenerationJob.seq==row.seq)
                .values(status='error',seq=row.seq+1,events_json=_json(events),result_json=_json(result),
                        error=error,lease_token='',lease_until=0,updated_at=now))
        db.commit()
        return [row[0] for row in db.query(PlanGenerationJob.id).filter(PlanGenerationJob.status=='running',
                PlanGenerationJob.lease_until<=now,PlanGenerationJob.attempts<MAX_ATTEMPTS).order_by(PlanGenerationJob.created_at).limit(20)]


async def _scheduler_loop():
    while True:
        try:
            for job_id in _maintenance(): _start_job(job_id)
        except Exception: logger.exception('Planning scheduler failed')
        await asyncio.sleep(1)


def start_scheduler():
    global _scheduler,_stopping
    _stopping = False
    _scheduler = asyncio.create_task(_scheduler_loop())


async def stop_scheduler():
    global _scheduler,_stopping
    _stopping = True
    tasks = [job.task for job in list(_jobs.values()) if job.task]
    if _scheduler: tasks.append(_scheduler); _scheduler = None
    for task in tasks: task.cancel()
    if tasks: await asyncio.gather(*tasks,return_exceptions=True)
    _jobs.clear()


def clean_messages(msgs):
    out = []
    for m in (msgs or [])[:MAX_SNAPSHOT_MSGS]:
        if isinstance(m,dict): out.append({'role':str(m.get('role') or 'user')[:10],'text':str(m.get('text') or '')[:MAX_MSG_TEXT]})
    return out
