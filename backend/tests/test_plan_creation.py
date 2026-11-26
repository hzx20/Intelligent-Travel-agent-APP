from concurrent.futures import ThreadPoolExecutor
import json
import time
from uuid import uuid4
import pytest
from sqlalchemy import event, text
from app.db.models import PlanCreationRequest, PlanGenerationJob, AiPlan
from app.services import plan_jobs as jobs
from test_trips import account

PATH='/api/plan/jobs/create'
def setup(client,monkeypatch):
    c,sessions=client;auth=account(c,'plan_creation')
    monkeypatch.setattr(jobs,'_start_job',lambda _:None)
    body={'request_id':str(uuid4()),'user_input':'固定请求','session_id':'creation-test','messages':[{'role':'user','text':'固定请求'}]}
    return c,sessions,auth,body


def test_creation_replays_same_job_and_binds_every_input_and_account(client,monkeypatch):
    c,sessions,auth,body=setup(client,monkeypatch)
    assert c.get('/api/plan/jobs/capabilities').json()['reliable_creation'] is True
    assert c.post(PATH,headers=auth,json={'user_input':'缺标识'}).status_code==422
    first=c.post(PATH,headers=auth,json=body);again=c.post(PATH,headers=auth,json=body)
    assert first.status_code==201 and again.status_code==200 and first.json()==again.json()
    assert first.json()['request_id']==body['request_id'] and first.headers['cache-control']=='no-store'
    for changed in ({'user_input':'变内容'},{'session_id':'other'},{'prev_collected':{'days':3}},{'messages':[{'role':'user','text':'改快照'}]},{'history':[{'role':'user','content':'新历史'}]}):
        assert c.post(PATH,headers=auth,json={**body,**changed}).status_code==409
    other=account(c,'other_plan_creator');second=c.post(PATH,headers=other,json=body)
    assert second.status_code==201 and second.json()['job_id']!=first.json()['job_id']
    assert c.get('/api/plan/jobs/'+first.json()['job_id'],headers=other).status_code==404
    with sessions() as db:assert db.query(PlanGenerationJob).count()==db.query(PlanCreationRequest).count()==2


def test_concurrent_creation_is_once_and_postcommit_kick_failure_keeps_mapping(client,monkeypatch):
    c,sessions,auth,body=setup(client,monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(lambda _:c.post(PATH,headers=auth,json=body),range(2)))
    assert sorted(r.status_code for r in responses)==[200,201] and responses[0].json()==responses[1].json()
    def fail(_):raise RuntimeError('synthetic scheduler launch failure')
    monkeypatch.setattr(jobs,'_start_job',fail)
    another={**body,'request_id':str(uuid4())};created=c.post(PATH,headers=auth,json=another)
    assert created.status_code==201 and c.post(PATH,headers=auth,json=another).json()==created.json()
    with sessions() as db:assert db.query(PlanCreationRequest).count()==db.query(PlanGenerationJob).count()==2


def test_guests_require_separate_secret_and_do_not_persist_it(client,monkeypatch):
    c,sessions,auth,body=setup(client,monkeypatch)
    assert c.post(PATH,json=body).status_code==422
    guest_key=str(uuid4());guest={**body,'guest_key':guest_key}
    a=c.post(PATH,json=guest);again=c.post(PATH,json=guest)
    assert a.status_code==201 and again.status_code==200 and a.json()==again.json() and a.json()['owner_id']==0
    b=c.post(PATH,json={**guest,'guest_key':str(uuid4())})
    assert b.status_code==201 and b.json()['job_id']!=a.json()['job_id']
    assert c.post(PATH,headers=auth,json=guest).status_code==422
    assert c.get('/api/plan/jobs/active').json()['job'] is None
    with sessions() as db:
        assert all(guest_key not in row.request_json for row in db.query(PlanGenerationJob))
        assert all(guest_key not in row.owner_key for row in db.query(PlanCreationRequest))


def test_cancellation_before_creation_is_persistent_and_never_starts_a_job(client,monkeypatch):
    c,sessions,auth,body=setup(client,monkeypatch)
    reply=c.post(PATH+'/cancel',headers=auth,json=body)
    assert reply.status_code==200 and reply.json()['status']=='cancelled' and reply.json()['job_id'] is None
    assert c.post(PATH,headers=auth,json=body).status_code==410
    assert c.post(PATH+'/cancel',headers=auth,json=body).json()==reply.json()
    assert c.post(PATH+'/cancel',headers=auth,json={**body,'user_input':'不同'}).status_code==409
    with sessions() as db:assert db.query(PlanCreationRequest).count()==1 and db.query(PlanGenerationJob).count()==0


def test_create_and_cancel_race_cannot_resurrect_or_save_history(client,monkeypatch):
    c,sessions,auth,body=setup(client,monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        create=pool.submit(c.post,PATH,headers=auth,json=body)
        cancel=pool.submit(c.post,PATH+'/cancel',headers=auth,json=body)
        created,cancelled=create.result(),cancel.result()
    assert created.status_code in (201,410) and cancelled.status_code==200 and cancelled.json()['status']=='cancelled'
    repeat=c.post(PATH,headers=auth,json=body)
    assert repeat.status_code==410 or repeat.status_code==200 and repeat.json()['status']=='cancelled'
    with sessions() as db:
        assert db.query(PlanCreationRequest).count()==1 and db.query(PlanGenerationJob).count()<=1 and db.query(AiPlan).count()==0
        for job in db.query(PlanGenerationJob):assert job.status=='cancelled' and jobs._claim(job.id) is None


def test_expiry_and_hard_delete_leave_tombstones_and_valid_foreign_keys(client,monkeypatch):
    c,sessions,auth,body=setup(client,monkeypatch)
    created=c.post(PATH,headers=auth,json=body).json()
    with sessions() as db:
        row=db.get(PlanGenerationJob,created['job_id']);row.status='done';row.updated_at=time.time()-jobs.JOB_TTL_SEC-1;db.commit()
    assert c.post(PATH,headers=auth,json=body).status_code==410
    expired=c.post(PATH+'/cancel',headers=auth,json=body)
    assert expired.status_code==200 and expired.json()['status']=='expired' and expired.json()['job_id'] is None
    jobs._maintenance()
    with sessions() as db:
        assert db.query(PlanCreationRequest).one().job_id is None
        assert db.execute(text('PRAGMA foreign_key_check')).fetchall()==[]
    assert c.post(PATH,headers=auth,json=body).status_code==410
    other={**body,'request_id':str(uuid4())};job=c.post(PATH,headers=auth,json=other).json()
    with sessions() as db:db.delete(db.get(PlanGenerationJob,job['job_id']));db.commit()
    assert c.post(PATH,headers=auth,json=other).status_code==410


def test_receipt_and_task_rollback_together_on_failure(client,monkeypatch):
    c,sessions,auth,body=setup(client,monkeypatch)
    def fail(*_):raise RuntimeError('synthetic receipt failure')
    event.listen(PlanCreationRequest,'before_insert',fail)
    try:
        with pytest.raises(RuntimeError,match='synthetic receipt'):c.post(PATH,headers=auth,json=body)
    finally:event.remove(PlanCreationRequest,'before_insert',fail)
    with sessions() as db:assert db.query(PlanGenerationJob).count()==db.query(PlanCreationRequest).count()==0
    assert c.post(PATH,headers=auth,json=body).status_code==201
