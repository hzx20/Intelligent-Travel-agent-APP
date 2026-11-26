"""Database claims, stale executor fencing and atomic original-plan history."""
from concurrent.futures import ThreadPoolExecutor
import json
import time
import pytest
from sqlalchemy import event, update
from app.db.models import AiPlan, PlanGenerationJob, User
from app.security import create_token
from app.services import plan_jobs as jobs


def pending(client,monkeypatch,uid=0):
    c,sessions=client
    monkeypatch.setattr(jobs,'_start_job',lambda _:None)
    headers={'Authorization':'Bearer '+create_token(uid)} if uid else {}
    reply=c.post('/api/plan/jobs',headers=headers,json={'user_input':'恢复同一次规划','session_id':'durable-test','messages':[{'role':'user','text':'恢复同一次规划'}]})
    assert reply.status_code==200
    assert reply.headers['cache-control']=='no-store'
    return c,sessions,reply.json()['job_id'],headers


def test_claim_is_exclusive_and_old_executor_cannot_write_after_recovery(client,monkeypatch):
    c,sessions,jid,headers=pending(client,monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool: claimed=list(pool.map(lambda _:jobs._claim(jid),range(2)))
    assert sum(job is not None for job in claimed)==1
    old=next(job for job in claimed if job)
    old.emit('token',{'text':'旧片段'})
    assert c.get(f'/api/plan/jobs/{jid}',headers=headers).json()['text']=='旧片段'
    with sessions() as db:
        db.execute(update(PlanGenerationJob).where(PlanGenerationJob.id==jid).values(lease_until=time.time()-1));db.commit()
    new=jobs._claim(jid)
    assert new.attempts==2 and new.lease_token!=old.lease_token and new.text==''
    assert new.events[-1]['type']=='reset'
    with pytest.raises(jobs.LostLease):old.emit('token',{'text':'迟到片段'})
    with pytest.raises(jobs.LostLease):jobs._finish(old,{'done':True,'itinerary':{'days':[]}})
    jobs._finish(new,{'done':False,'question':'恢复后的追问'})
    snap=c.get(f'/api/plan/jobs/{jid}').json()
    assert snap['status']=='done' and snap['seq']==3
    assert snap['result']['question']=='恢复后的追问'
    assert c.get(f'/api/plan/jobs/{jid}?since=2').json()['events']==[snap['events'][-1]]


def test_cancel_revokes_lease_and_prevents_late_history(client,monkeypatch):
    c,sessions=client
    with sessions() as db:
        u=User(username='cancel_durable',password_hash='unused');db.add(u);db.commit();uid=u.id
    c,sessions,jid,headers=pending(client,monkeypatch,uid)
    executor=jobs._claim(jid)
    assert c.delete(f'/api/plan/jobs/{jid}',headers=headers).status_code==200
    with pytest.raises(jobs.LostLease):jobs._finish(executor,{'done':True,'itinerary':{'days':[{'day':1}]}},final={})
    assert jobs._claim(jid) is None
    with sessions() as db:assert db.query(AiPlan).count()==0
    assert c.get(f'/api/plan/jobs/{jid}',headers=headers).json()['result']['cancelled'] is True


def test_exhausted_recovery_is_terminal_and_finished_jobs_expire(client,monkeypatch):
    c,sessions,jid,_=pending(client,monkeypatch)
    with sessions() as db:
        db.execute(update(PlanGenerationJob).where(PlanGenerationJob.id==jid).values(attempts=2,lease_until=0));db.commit()
    assert jid not in jobs._maintenance()
    snap=c.get(f'/api/plan/jobs/{jid}').json()
    assert snap['status']=='error' and '多次中断' in snap['result']['error']
    assert jobs._claim(jid) is None
    with sessions() as db:
        db.execute(update(PlanGenerationJob).where(PlanGenerationJob.id==jid).values(updated_at=time.time()-jobs.JOB_TTL_SEC-1));db.commit()
    assert c.get(f'/api/plan/jobs/{jid}').status_code==404
    jobs._maintenance()
    with sessions() as db:assert db.get(PlanGenerationJob,jid) is None


def test_completion_and_history_commit_once_and_failed_history_preserves_result(client,monkeypatch):
    c,sessions=client
    with sessions() as db:
        user=User(username='atomic_plan',password_hash='unused');db.add(user);db.commit();uid=user.id
    _,_,jid,headers=pending(client,monkeypatch,uid)
    job=jobs._claim(jid);result={'done':True,'itinerary':{'title':'唯一规划','days':[{'day':1}]}}
    jobs._finish(job,result,final={'collected':{'destination':'杭州'}})
    with pytest.raises(jobs.LostLease):jobs._finish(job,result,final={})
    with sessions() as db:
        assert db.query(AiPlan).count()==1
        assert json.loads(db.get(PlanGenerationJob,jid).result_json)['saved'] is True
    _,_,failed_id,_=pending(client,monkeypatch,uid);executor=jobs._claim(failed_id)
    def fail(*_):raise RuntimeError('synthetic history failure')
    event.listen(AiPlan,'before_insert',fail)
    try:jobs._finish(executor,result,final={'collected':{}})
    finally:event.remove(AiPlan,'before_insert',fail)
    snapshot=c.get(f'/api/plan/jobs/{failed_id}',headers=headers).json()
    assert snapshot['status']=='done' and snapshot['result']['saved'] is False
    assert snapshot['result']['itinerary']['title']=='唯一规划'
    with sessions() as db:assert db.query(AiPlan).count()==1


def test_persisted_input_is_bounded_and_guest_jobs_cannot_be_listed(client,monkeypatch):
    c,sessions,jid,_=pending(client,monkeypatch)
    assert c.get('/api/plan/jobs/active').json()=={'job':None}
    assert c.get(f'/api/plan/jobs/{jid}').headers['cache-control']=='no-store'
    before=0
    with sessions() as db:before=db.query(PlanGenerationJob).count()
    assert c.post('/api/plan/jobs',json={'user_input':'x','prev_collected':{'large':'x'*(256*1024)}}).status_code==422
    assert c.post('/api/plan/jobs',json={'user_input':'x','history':[{}]*201}).status_code==422
    with sessions() as db:assert db.query(PlanGenerationJob).count()==before
