import asyncio
from contextlib import closing
import json
import sqlite3
import subprocess
import sys
import time

import httpx
import pytest
from app.core import provider_budget as budget


def test_atomic_capacity_start_spacing_expiry_and_token_isolation(tmp_path):
    now=[100.];store=budget.BudgetStore(tmp_path/'budget.db',clock=lambda:now[0])
    assert store.claim('amap','first',1,.4,30)
    assert not store.claim('amap','second',1,.4,30)
    assert store.claim('chat','chat',1,.4,30)
    store.release('wrong-token')
    assert not store.claim('amap','second',1,.4,30)
    store.release('first')
    assert not store.claim('amap','second',1,.4,30)
    now[0]=100.4
    assert store.claim('amap','second',1,.4,30)
    now[0]=131
    assert not store.renew('second',30)
    assert store.claim('amap','new',1,.4,30)


def test_heartbeat_retains_slot_and_release_stops_thread(tmp_path):
    store=budget.BudgetStore(tmp_path/'budget.db')
    lease=budget.Lease(store,'amap',1,0,ttl=.15)
    lease.acquire()
    try:
        time.sleep(.35)
        assert not store.claim('amap','other',1,0,.15)
        lease.ensure()
    finally:lease.release()
    assert not lease.thread.is_alive()
    assert store.claim('amap','other',1,0,.15)


def test_bad_store_prevents_provider_and_no_secrets_in_error(tmp_path,monkeypatch):
    path=tmp_path/'bad.db';path.write_bytes(b'broken')
    monkeypatch.setenv('TRAVEL_PROVIDER_BUDGET','1')
    monkeypatch.setattr(budget,'make_lease',lambda provider:budget.Lease(budget.BudgetStore(path),provider,1,0))
    calls=[]
    monkeypatch.setattr(budget.httpx,'get',lambda *a,**k:calls.append(1))
    with pytest.raises(httpx.RequestError):budget.amap_get('https://example.invalid',params={'key':'secret-test'})
    assert not calls


def test_sync_exception_and_async_cancellation_release_slots(tmp_path,monkeypatch):
    store=budget.BudgetStore(tmp_path/'budget.db')
    monkeypatch.setenv('TRAVEL_PROVIDER_BUDGET','1')
    monkeypatch.setattr(budget,'make_lease',lambda provider:budget.Lease(store,provider,1,0))
    with pytest.raises(ValueError):
        with budget.provider_slot('amap'):raise ValueError('provider failed')
    async def exercise():
        ready=asyncio.Event()
        async def hold():
            async with budget.async_provider_slot('chat'):
                ready.set();await asyncio.sleep(10)
        task=asyncio.create_task(hold());await ready.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        for _ in range(30):
            with closing(store.connect()) as db:count=db.execute('SELECT COUNT(*) FROM slots').fetchone()[0]
            if count==0:break
            await asyncio.sleep(.01)
        assert count==0
    asyncio.run(exercise())


def test_real_processes_share_capacity_and_provider_start_gap(tmp_path):
    path=tmp_path/'budget.db'
    code=r'''import json,sys,time
from contextlib import closing
from app.core.provider_budget import BudgetStore,Lease
store=BudgetStore(sys.argv[1]);lease=Lease(store,'amap',1,.1,wait=5)
lease.acquire()
try:
 with closing(store.connect()) as db:count=db.execute('SELECT COUNT(*) FROM slots').fetchone()[0]
 print(json.dumps({'start':time.time(),'count':count}),flush=True)
 time.sleep(.15)
finally:lease.release()
'''
    processes=[subprocess.Popen([sys.executable,'-c',code,str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
                               creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) for _ in range(4)]
    rows=[]
    try:
        for process in processes:
            out,err=process.communicate(timeout=15);assert process.returncode==0,err;rows.append(json.loads(out))
    finally:
        for process in processes:
            if process.poll() is None:process.kill();process.wait()
    assert all(r['count']==1 for r in rows)
    starts=sorted(r['start'] for r in rows)
    assert all(b-a>=.09 for a,b in zip(starts,starts[1:]))


def test_llm_and_ocr_use_async_budget_without_sdk_hidden_retries(tmp_path,monkeypatch):
    from app.core import llm
    from app.services import guide_ocr
    store=budget.BudgetStore(tmp_path/'budget.db');groups=[];options=[]
    monkeypatch.setenv('TRAVEL_PROVIDER_BUDGET','1')
    def lease(provider):groups.append(provider);return budget.Lease(store,provider,1,0)
    monkeypatch.setattr(budget,'make_lease',lease)
    monkeypatch.setattr(llm.settings,'zhipu_api_key','test-not-real')
    class Model:
        def __init__(self,**kw):options.append(kw)
        async def ainvoke(self,messages):
            from types import SimpleNamespace
            return SimpleNamespace(content='synthetic result')
    monkeypatch.setattr(llm,'ChatOpenAI',Model)
    class Client:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,*args,**kwargs):return httpx.Response(200,json={'status':'succeeded','words_result':[{'words':'synthetic OCR'}]})
    monkeypatch.setattr(guide_ocr.httpx,'AsyncClient',Client)
    async def exercise():
        assert await llm.chat([{'role':'user','content':'synthetic'}])=='synthetic result'
        assert await guide_ocr.recognize(b'synthetic','image/png')=='synthetic OCR'
    asyncio.run(exercise())
    assert groups==['chat','ocr'] and options[0]['max_retries']==0
    with closing(store.connect()) as db:assert db.execute('SELECT COUNT(*) FROM slots').fetchone()[0]==0


def test_lost_heartbeat_does_not_release_another_worker_slot(tmp_path):
    now=[100.];store=budget.BudgetStore(tmp_path/'budget.db',clock=lambda:now[0])
    lease=budget.Lease(store,'amap',1,0,ttl=.09);lease.acquire()
    try:
        now[0]=101
        assert store.claim('amap','replacement',1,0,30)
        assert lease.lost.wait(1)
        with pytest.raises(budget.ProviderBusy):lease.ensure()
    finally:lease.release()
    with closing(store.connect()) as db:
        assert db.execute('SELECT token FROM slots').fetchall()==[('replacement',)]


def test_cancel_during_acquisition_never_orphans_a_heartbeat(tmp_path,monkeypatch):
    store=budget.BudgetStore(tmp_path/'budget.db');assert store.claim('ocr','held',1,0,30)
    monkeypatch.setenv('TRAVEL_PROVIDER_BUDGET','1')
    lease=budget.Lease(store,'ocr',1,0,wait=.1)
    monkeypatch.setattr(budget,'make_lease',lambda provider:lease)
    async def exercise():
        async def queued():
            async with budget.async_provider_slot('ocr'):raise AssertionError('must not start')
        task=asyncio.create_task(queued());await asyncio.sleep(.02);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    asyncio.run(exercise())
    assert lease.thread is None
    with closing(store.connect()) as db:assert db.execute('SELECT token FROM slots').fetchall()==[('held',)]
