"""Original /plan jobs survive actual process replacement and shared workers."""
import json
import socket
import sqlite3
import time
from uuid import uuid4
from test_service_restart import running_backend

BOOTSTRAP = '''
import asyncio,sys,uvicorn
from app.main import app
from app.services import plan_jobs
async def graph(state):
    if sys.argv[2]=='interrupt':
        yield 'clarify',{'done':False,'question':'重启前的片段'},[]
        await asyncio.sleep(120)
    yield 'clarify',{'done':True,'collected':{'destination':'杭州'}},['隔离澄清完成']
    await asyncio.sleep(0.2)
    yield 'generate',{'itinerary':{'title':'持久化原规划','days':[{'day':1,'spots':[]}]}},['隔离生成完成']
plan_jobs.iter_updates=graph
uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[1]),log_level='warning')
'''

def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]


def until(read,predicate):
    deadline=time.monotonic()+12
    while True:
        value=read()
        if predicate(value):return value
        assert time.monotonic()<deadline,value
        time.sleep(0.05)


def test_original_plan_running_completed_guest_and_cancelled_jobs_survive_restart(tmp_path):
    data=tmp_path/'isolated-plan-data';server_port=port()
    with running_backend(data,server_port,bootstrap=BOOTSTRAP,bootstrap_args=('interrupt',)) as c:
        assert c.post('/api/auth/register',json={'username':'durable_restart','password':'isolated-password'}).status_code==201
        auth={'Authorization':'Bearer '+c.post('/api/auth/login',json={'username':'durable_restart','password':'isolated-password'}).json()['token']}
        body={'user_input':'原规划重启验收','session_id':'restart-original','messages':[{'role':'user','text':'原规划重启验收'}]}
        jid=c.post('/api/plan/jobs',headers=auth,json=body).json()['job_id']
        guest=c.post('/api/plan/jobs',json={**body,'session_id':'guest-original'}).json()['job_id']
        cancelled=c.post('/api/plan/jobs',headers=auth,json={**body,'session_id':'cancel-original'}).json()['job_id']
        assert c.delete(f'/api/plan/jobs/{cancelled}',headers=auth).status_code==200
        first=until(lambda:c.get(f'/api/plan/jobs/{jid}',headers=auth).json(),lambda s:s['text']=='重启前的片段')
        assert first['status']=='running' and c.get('/api/plan/jobs/active',headers=auth).json()['job']['job_id']==jid
    # Only the isolated pytest DB is adjusted; no wait or edit to business DB.
    with sqlite3.connect(data/'app.db') as db:
        db.execute("UPDATE plan_generation_jobs SET lease_until=0 WHERE status='running'");db.commit()
    with running_backend(data,server_port,bootstrap=BOOTSTRAP,bootstrap_args=('complete',)) as c:
        done=until(lambda:c.get(f'/api/plan/jobs/{jid}',headers=auth).json(),lambda s:s['status']=='done')
        assert done['result']['saved'] is True and done['result']['itinerary']['title']=='持久化原规划'
        assert any(e['type']=='reset' and e['seq']>first['seq'] for e in done['events'])
        assert [e['seq'] for e in done['events']]==list(range(1,done['seq']+1))
        assert c.get(f'/api/plan/jobs/{jid}').status_code==404
        guest_done=until(lambda:c.get(f'/api/plan/jobs/{guest}').json(),lambda s:s['status']=='done')
        assert guest_done['result']['saved'] is False
        assert c.get(f'/api/plan/jobs/{guest}',headers=auth).status_code==404
        assert c.get('/api/plan/jobs/active').json()['job'] is None
        assert c.get(f'/api/plan/jobs/{cancelled}',headers=auth).json()['status']=='cancelled'
        assert len(c.get('/api/plan/history',headers=auth).json()['items'])==1
    with running_backend(data,server_port,bootstrap=BOOTSTRAP,bootstrap_args=('complete',)) as c:
        assert c.get(f'/api/plan/jobs/{jid}',headers=auth).json()==done
        assert c.get(f'/api/plan/jobs/{guest}').json()==guest_done
        assert len(c.get('/api/plan/history',headers=auth).json()['items'])==1
        assert c.get('/api/plan/jobs/active',headers=auth).json()['job'] is None
    with sqlite3.connect(data/'app.db') as db:
        assert db.execute('SELECT attempts FROM plan_generation_jobs WHERE id=?',(jid,)).fetchone()==(2,)


def test_two_actual_workers_share_one_queue_and_one_saved_history(tmp_path):
    data=tmp_path/'shared-plan-data'
    with running_backend(data,port(),bootstrap=BOOTSTRAP,bootstrap_args=('complete',)) as first:
        assert first.post('/api/auth/register',json={'username':'shared_worker','password':'isolated-password'}).status_code==201
        auth={'Authorization':'Bearer '+first.post('/api/auth/login',json={'username':'shared_worker','password':'isolated-password'}).json()['token']}
        uid=first.get('/api/auth/me',headers=auth).json()['id']
        with running_backend(data,port(),bootstrap=BOOTSTRAP,bootstrap_args=('complete',)) as second:
            jid=uuid4().hex;now=time.time()
            with sqlite3.connect(data/'app.db') as db:
                db.execute('INSERT INTO plan_generation_jobs (id,user_id,session_id,status,seq,events_json,text,result_json,request_json,plan_id,error,attempts,lease_token,lease_until,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (jid,uid,'shared-worker-test','running',0,'[]','',None,json.dumps({'user_input':'共享队列验收'}),None,'',0,'',0,now,now));db.commit()
            done=until(lambda:first.get(f'/api/plan/jobs/{jid}',headers=auth).json(),lambda s:s['status']=='done')
            assert second.get(f'/api/plan/jobs/{jid}',headers=auth).json()==done
            assert len(first.get('/api/plan/history',headers=auth).json()['items'])==1
            with sqlite3.connect(data/'app.db') as db:
                assert db.execute('SELECT attempts FROM plan_generation_jobs WHERE id=?',(jid,)).fetchone()==(1,)


def test_creation_receipts_survive_restart_expiry_and_cancellation_before_create(tmp_path):
    data=tmp_path/'receipt-restart-data';server_port=port()
    body={'request_id':str(uuid4()),'user_input':'可靠创建重启验收'}
    cancelled={**body,'request_id':str(uuid4())}
    guest={**body,'request_id':str(uuid4()),'guest_key':str(uuid4())}
    with running_backend(data,server_port,bootstrap=BOOTSTRAP,bootstrap_args=('complete',)) as c:
        assert c.post('/api/auth/register',json={'username':'receipt_restart','password':'isolated-password'}).status_code==201
        auth={'Authorization':'Bearer '+c.post('/api/auth/login',json={'username':'receipt_restart','password':'isolated-password'}).json()['token']}
        first=c.post('/api/plan/jobs/create',headers=auth,json=body);assert first.status_code==201
        jid=first.json()['job_id'];done=until(lambda:c.get(f'/api/plan/jobs/{jid}',headers=auth).json(),lambda s:s['status']=='done')
        pid=done['plan_id'];revision=done['result']['plan_revision']
        assert c.patch(f'/api/plan/{pid}/snapshot/edit',headers=auth,json={'version':1,'revision':revision,'messages':[{'role':'ai','text':'重启保留快照'}],'map_state':{'center':[120,30],'zoom':12}}).json()['version']==2
        deleted=c.delete(f'/api/plan/{pid}/content',headers=auth,params={'version':2,'revision':revision})
        assert deleted.status_code==200 and deleted.json()['version']==3
        assert c.post(f'/api/plan/{pid}/content/restore',headers=auth,json={'version':3,'revision':revision}).json()['version']==4
        conversion={'version':4,'revision':revision}
        converted=c.post(f'/api/trips/from-plan/{pid}/convert',headers=auth,json=conversion)
        assert converted.status_code==201
        converted_id=converted.json()['trip']['id']
        assert c.patch(f'/api/trips/{converted_id}',headers=auth,json={'version':1,'title':'转换后重启保留'}).status_code==200
        from test_trip_guest_import import sample
        guest_copy=sample()
        copied=c.post('/api/trips/import-guest',headers=auth,json=guest_copy).json()['trip']
        anonymous=c.post('/api/plan/jobs/create',json=guest).json()
        until(lambda:c.get('/api/plan/jobs/'+anonymous['job_id']).json(),lambda s:s['status']=='done')
        assert c.post('/api/plan/jobs/create/cancel',headers=auth,json=cancelled).json()['status']=='cancelled'
    with running_backend(data,server_port,bootstrap=BOOTSTRAP,bootstrap_args=('complete',)) as c:
        replay=c.post('/api/plan/jobs/create',headers=auth,json=body)
        assert replay.status_code==200 and replay.json()['job_id']==jid and replay.json()['status']=='done'
        assert c.post('/api/plan/jobs/create',json=guest).json()['job_id']==anonymous['job_id']
        assert c.post('/api/plan/jobs/create',headers=auth,json=cancelled).status_code==410
        assert len(c.get('/api/plan/history',headers=auth).json()['items'])==1
        snapshot=c.get(f'/api/plan/{pid}',headers=auth).json()
        assert snapshot['version']==4 and snapshot['revision']==revision and snapshot['chat']==[{'role':'ai','text':'重启保留快照'}] and snapshot['map_state']['zoom']==12
        conversion_replay=c.post(f'/api/trips/from-plan/{pid}/convert',headers=auth,json=conversion)
        assert conversion_replay.status_code==200 and conversion_replay.json()['trip']['id']==converted_id
        assert conversion_replay.json()['trip']['title']=='转换后重启保留'
        assert c.get(f'/api/trips/from-plan/{pid}/conversion',headers=auth,params={'revision':revision}).json()['trip']['id']==converted_id
        assert c.post('/api/trips/import-guest/check',headers=auth,json=guest_copy).json()['trip']['id']==copied['id']
        assert len(c.get('/api/trips',headers=auth).json()['items'])==2
    with sqlite3.connect(data/'app.db') as db:
        db.execute("UPDATE plan_generation_jobs SET updated_at=0 WHERE id=?",(jid,));db.commit()
    with running_backend(data,server_port,bootstrap=BOOTSTRAP,bootstrap_args=('complete',)) as c:
        assert c.post('/api/plan/jobs/create',headers=auth,json=body).status_code==410
        assert c.post('/api/plan/jobs/create',headers=auth,json=cancelled).status_code==410
        assert len(c.get('/api/plan/history',headers=auth).json()['items'])==1
