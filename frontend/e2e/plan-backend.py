"""Fake only graph output, using the actual durable scheduler and HTTP app."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time
root=Path(os.environ['TRAVEL_DATA_DIR'])
assert root.is_absolute() and root.parent.name.startswith('travel-browser-accounts-')
assert not (root/'app.db').exists()
assert not os.environ.get('AMAP_API_KEY') and not os.environ.get('ZHIPU_API_KEY')
sys.path.insert(0,str(Path.cwd()))
from fastapi import Depends, HTTPException
from sqlalchemy import update
from app.main import app
from app.db.models import PlanGenerationJob, AiPlan, User
from app.db.database import SessionLocal
from app.routers.auth import get_current_user_optional
from app.services import plan_jobs

async def graph(state):
    text=state['user_input']
    if '故障' in text:raise RuntimeError('synthetic model failure')
    if '追问' in text:
        yield 'clarify',{'done':False,'question':'请补充出发日期。'},['隔离追问已生成']
        await asyncio.sleep(2)
        return
    if '恢复' in text:
        with SessionLocal() as db:
            attempts=max(row.attempts for row in db.query(PlanGenerationJob) if json.loads(row.request_json).get('user_input')==text)
        if attempts==1:
            yield 'clarify',{'done':False,'question':'中断前的片段'},[]
            await asyncio.sleep(120)
    yield 'clarify',{'done':True,'collected':{'destination':'杭州'}},['隔离需求已收集']
    await asyncio.sleep(120 if '等待' in text else 3 if '慢速' in text else 0.25)
    title='历史方案甲' if '历史甲' in text else '历史方案乙' if '历史乙' in text else '隔离原规划结果'
    yield 'generate',{'itinerary':{'title':title,'summary':'合成数据验收','days':[{'day':1,'theme':'测试安排','spots':[]}]}},['隔离行程已生成']
plan_jobs.iter_updates=graph

@app.post('/api/__qa/plan/recover/{jid}')
async def recover(jid:str,user:User|None=Depends(get_current_user_optional)):
    job=plan_jobs.get_job(jid,user.id if user else 0)
    if job is None:raise HTTPException(404)
    if job.status!='running':raise HTTPException(409)
    runtime=plan_jobs._jobs.get(jid)
    if runtime and runtime.task:
        runtime.task.cancel();await asyncio.gather(runtime.task,return_exceptions=True)
    with SessionLocal() as db:
        changed=db.execute(update(PlanGenerationJob).where(PlanGenerationJob.id==jid,PlanGenerationJob.status=='running')
            .values(lease_until=time.time()-1))
        db.commit()
        if changed.rowcount!=1:raise HTTPException(409)
    return {'ok':True}

import uvicorn

@app.post('/api/__qa/plan/expire-undo/{pid}')
def expire_undo(pid:int,user:User|None=Depends(get_current_user_optional)):
    if user is None:raise HTTPException(401)
    with SessionLocal() as db:
        p=db.get(AiPlan,pid)
        if p is None or p.user_id!=user.id:raise HTTPException(404)
        if p.status!='deleted':raise HTTPException(409)
        p.restore_until=time.time()-1;db.commit()
    return {'ok':True}

@app.post('/api/__qa/plan/expire/{jid}')
def expire(jid:str,user:User|None=Depends(get_current_user_optional)):
    job=plan_jobs.get_job(jid,user.id if user else 0)
    if job is None:raise HTTPException(404)
    if job.status=='running':raise HTTPException(409)
    with SessionLocal() as db:
        db.execute(update(PlanGenerationJob).where(PlanGenerationJob.id==jid).values(updated_at=0));db.commit()
    return {'ok':True}

uvicorn.run(app,host='127.0.0.1',port=18963,log_level='warning')
