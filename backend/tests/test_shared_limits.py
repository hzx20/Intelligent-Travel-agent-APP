import asyncio
import socket
import sqlite3
import sys
from pathlib import Path
import subprocess

import httpx
import pytest
from fastapi import FastAPI
from app.core.shared_limits import SharedQuota, SharedPublicLimits, LimitStoreUnavailable
from test_service_restart import running_backend


def test_sliding_window_scopes_expiry_and_no_raw_peer(tmp_path):
    clock = [100.0]
    path = tmp_path/'quota.db'
    store = SharedQuota(path, clock=lambda:clock[0])
    assert store.claim('ocr','192.0.2.1',2,60) == 0
    clock[0] = 130
    assert store.claim('ocr','192.0.2.1',2,60) == 0
    assert store.claim('ocr','192.0.2.1',2,60) == 30
    assert store.claim('ocr','192.0.2.2',2,60) == 0
    assert store.claim('route','192.0.2.1',2,60) == 0
    clock[0] = 160
    assert store.claim('ocr','192.0.2.1',2,60) == 0
    assert store.claim('ocr','192.0.2.1',2,60) == 30
    with sqlite3.connect(path) as db:
        assert all('192.0.2.' not in x[0] for x in db.execute('SELECT scope FROM quota_events'))


def test_capacity_never_evicts_active_budget_and_bad_store_fails_closed(tmp_path):
    store = SharedQuota(tmp_path/'quota.db', max_events=1)
    assert store.claim('x','first',1,60) == 0
    with pytest.raises(LimitStoreUnavailable): store.claim('x','new-peer',1,60)
    assert store.claim('x','first',1,60) > 0
    path = tmp_path/'bad.db';path.write_bytes(b'not sqlite')
    with pytest.raises(LimitStoreUnavailable): SharedQuota(path).claim('x','peer',1,60)


def test_parallel_processes_share_one_atomic_budget(tmp_path):
    path=tmp_path/'quota.db'
    SharedQuota(path).claim('initial','warmup',1,60)
    code="from app.core.shared_limits import SharedQuota;import sys;print(SharedQuota(sys.argv[1]).claim('same','peer',3,60))"
    processes=[subprocess.Popen([sys.executable,'-c',code,str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
                               creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) for _ in range(8)]
    values=[]
    try:
        for p in processes:
            out,err=p.communicate(timeout=15)
            assert p.returncode == 0,err
            values.append(int(out))
    finally:
        for p in processes:
            if p.poll() is None:p.kill();p.wait()
    assert values.count(0)==3 and sum(x>0 for x in values)==5


@pytest.mark.parametrize('unavailable', [False, True])
def test_denied_request_does_not_read_image_body_and_health_is_unaffected(tmp_path, unavailable):
    class Denying:
        def claim(self,*args):
            if unavailable: raise LimitStoreUnavailable()
            return 12
    reads=[]
    async def receive(): reads.append(True);return {'type':'http.request','body':b'large-image','more_body':False}
    app=SharedPublicLimits(FastAPI(),Denying())
    async def exercise():
        sent=[]
        async def send(message):sent.append(message)
        await app({'type':'http','method':'POST','path':'/api/trips/guest-guide-image-text','client':('127.0.0.1',1),'headers':[]},receive,send)
        assert sent[0]['status']==(503 if unavailable else 429)
        assert (b'retry-after',b'1' if unavailable else b'12') in sent[0]['headers']
    asyncio.run(exercise());assert not reads


def free_port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]


def test_two_real_servers_and_restart_share_guest_route_budget(tmp_path):
    bootstrap="""import os,sys
os.environ['TRAVEL_SHARED_LIMITS']='true'
from app.routers import map as routes
routes.trip_routes.get_route=lambda *a:{'distance_m':1}
import uvicorn
uvicorn.run('app.main:app',host='127.0.0.1',port=int(sys.argv[1]),log_level='warning',proxy_headers=False)
"""
    data=tmp_path/'data'
    body={'origin':{'lng':120,'lat':30},'destination':{'lng':121,'lat':31}}
    with running_backend(data,free_port(),bootstrap=bootstrap) as a:
        with running_backend(data,free_port(),bootstrap=bootstrap) as b:
            for index in range(30):
                c=a if index%2 else b
                assert c.post('/api/map/guest-route',json=body).status_code == 200
            blocked=b.post('/api/map/guest-route',json=body,headers={'X-Forwarded-For':'203.0.113.10'})
            assert blocked.status_code == 429 and int(blocked.headers['retry-after']) > 0
            assert a.get('/api/health').status_code == 200
    with running_backend(data,free_port(),bootstrap=bootstrap) as c:
        assert c.post('/api/map/guest-route',json=body).status_code == 429
        assert c.get('/api/health').status_code == 200
