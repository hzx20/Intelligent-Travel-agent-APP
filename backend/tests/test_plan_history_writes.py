import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from app.db.models import AiPlan
from test_trips import account

def setup(client):
    c,sessions=client;auth=account(c,'plan_history_writes');uid=c.get('/api/auth/me',headers=auth).json()['id']
    with sessions() as db:
        p=AiPlan(user_id=uid,session_id='history-test',status='completed',result_json='{"title":"历史原文","days":[]}');db.add(p);db.commit();pid=p.id
    path=f'/api/plan/{pid}';d=c.get(path,headers=auth).json()
    return c,sessions,auth,path,{'version':d['version'],'revision':d['revision']},uid

def test_snapshot_cas_binding_validation_and_legacy_guard(client):
    c,sessions,auth,path,v,_=setup(client)
    assert c.patch(path+'/snapshot',headers=auth,json={'messages':[]}).status_code==428
    a=c.patch(path+'/snapshot/edit',headers=auth,json={**v,'messages':[{'role':'user','text':'新快照'}]})
    assert a.status_code==200 and a.json()['version']==2 and a.headers['cache-control']=='no-store'
    assert c.patch(path+'/snapshot/edit',headers=auth,json={**v,'messages':[{'role':'user','text':'陈旧'}]}).status_code==409
    assert c.patch(path+'/snapshot/edit',headers=auth,json={**v,'version':2,'revision':str(uuid4()),'messages':[]}).status_code==409
    assert c.get(path,headers=auth).json()['chat']==[{'role':'user','text':'新快照'}]
    for map_state in ({'center':[181,30],'zoom':12},{'center':[1,2],'zoom':True},{'center':[True,30],'zoom':10}):
        assert c.patch(path+'/snapshot/edit',headers=auth,json={**v,'version':2,'map_state':map_state}).status_code==422
    empty=c.patch(path+'/snapshot/edit',headers=auth,json={**v,'version':2})
    assert empty.status_code==200 and empty.json()['version']==2

def test_concurrent_snapshot_and_delete_cannot_overwrite_each_other(client):
    c,sessions,auth,path,v,_=setup(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        save=pool.submit(c.patch,path+'/snapshot/edit',headers=auth,json={**v,'messages':[{'role':'user','text':'并发'}]})
        delete=pool.submit(c.delete,path+'/content',headers=auth,params=v)
        responses=[save.result(),delete.result()]
    assert sum(r.status_code==200 for r in responses)==1
    assert all(r.status_code in (200,404,409) for r in responses)
    assert c.get(path+'/state',headers=auth).json()['version']==2

def test_delete_retry_does_not_extend_undo_and_old_delete_cannot_redelete_after_restore(client):
    c,sessions,auth,path,v,_=setup(client)
    first=c.delete(path+'/content',params=v,headers=auth).json()
    again=c.delete(path+'/content',params=v,headers=auth).json()
    assert first['version']==again['version']==2 and first['restore_until']==again['restore_until']
    assert first['can_restore'] and first['restore_seconds_remaining']<=8
    assert c.get(path,headers=auth).status_code==404
    state=c.get(path+'/state',headers=auth);assert state.headers['cache-control']=='no-store' and state.json()['status']=='deleted'
    restored=c.post(path+'/content/restore',headers=auth,json={**v,'version':2})
    assert restored.status_code==200 and restored.json()['version']==3
    assert c.post(path+'/content/restore',headers=auth,json={**v,'version':2}).json()['version']==3
    assert c.delete(path+'/content',headers=auth,params=v).status_code==409
    assert c.get(path,headers=auth).status_code==200

def test_undo_deadline_is_enforced_on_server_and_legacy_deleted_has_no_implicit_window(client):
    c,sessions,auth,path,v,_=setup(client);c.delete(path+'/content',params=v,headers=auth)
    with sessions() as db:p=db.get(AiPlan,int(path.split('/')[-1]));p.restore_until=time.time()-1;db.commit()
    assert c.post(path+'/content/restore',headers=auth,json={**v,'version':2}).status_code==410
    assert c.delete(path+'/content',headers=auth,params=v).json()['can_restore'] is False
    with sessions() as db:p=db.get(AiPlan,int(path.split('/')[-1]));p.restore_until=None;db.commit()
    assert c.post(path+'/content/restore',headers=auth,json={**v,'version':2}).status_code==410

def test_reused_id_cannot_accept_old_snapshot_delete_or_restore(client):
    c,sessions,auth,path,v,uid=setup(client);pid=int(path.split('/')[-1])
    with sessions() as db:
        db.delete(db.get(AiPlan,pid));db.commit();db.add(AiPlan(id=pid,user_id=uid,session_id='replacement',status='completed'));db.commit()
    assert c.patch(path+'/snapshot/edit',headers=auth,json={**v,'messages':[]}).status_code==409
    assert c.delete(path+'/content',headers=auth,params=v).status_code==409
    assert c.post(path+'/content/restore',headers=auth,json=v).status_code==409

def test_history_state_and_writes_keep_owner_permissions(client):
    c,sessions,auth,path,v,_=setup(client);other=account(c,'other_history')
    assert c.get(path+'/state').status_code==401
    assert c.get(path+'/state',headers=other).status_code==404
    assert c.delete(path+'/content',headers=other,params=v).status_code==404
    assert c.post(path+'/content/restore',headers=other,json=v).status_code==404
    assert c.get('/api/plan/history',headers=auth).json()['items'][0]['revision']==v['revision']
