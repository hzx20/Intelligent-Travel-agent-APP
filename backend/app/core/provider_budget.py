"""Opt-in local-volume provider start pacing and leased concurrency budgets."""
import asyncio
from contextlib import closing, contextmanager, asynccontextmanager
import os
import math
from pathlib import Path
import secrets
import sqlite3
import threading
import time

import httpx
from starlette.concurrency import run_in_threadpool


class ProviderBusy(RuntimeError):
    pass


def enabled():
    return os.environ.get('TRAVEL_PROVIDER_BUDGET','').lower() in ('1','true','yes')


class BudgetStore:
    def __init__(self,path,*,clock=time.time):
        self.path=Path(path);self.clock=clock

    def connect(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        db=sqlite3.connect(self.path,timeout=.5,isolation_level=None)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS starts(provider TEXT PRIMARY KEY, next_start REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS slots(token TEXT PRIMARY KEY, provider TEXT NOT NULL, expires REAL NOT NULL)')
            return db
        except BaseException:
            db.close();raise

    def claim(self,provider,token,capacity,interval,ttl):
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE');now=self.clock()
            db.execute('DELETE FROM slots WHERE expires<=?',(now,))
            row=db.execute('SELECT next_start FROM starts WHERE provider=?',(provider,)).fetchone()
            count=db.execute('SELECT COUNT(*) FROM slots WHERE provider=?',(provider,)).fetchone()[0]
            if count>=capacity:db.rollback();return False
            if row and row[0]>now:db.rollback();return False
            db.execute('INSERT INTO slots VALUES(?,?,?)',(token,provider,now+ttl))
            db.execute('INSERT OR REPLACE INTO starts VALUES(?,?)',(provider,now+interval))
            db.commit();return True

    def renew(self,token,ttl):
        with closing(self.connect()) as db:
            now=self.clock()
            return db.execute('UPDATE slots SET expires=? WHERE token=? AND expires>?',(now+ttl,token,now)).rowcount==1

    def release(self,token):
        with closing(self.connect()) as db:db.execute('DELETE FROM slots WHERE token=?',(token,))


class Lease:
    def __init__(self,store,provider,capacity,interval,*,ttl=30,wait=2):
        self.store=store;self.provider=provider;self.capacity=capacity;self.interval=interval
        self.ttl=ttl;self.wait=wait;self.token=secrets.token_hex(16)
        self.stop=threading.Event();self.lost=threading.Event();self.thread=None

    def acquire(self):
        deadline=time.monotonic()+self.wait
        try:
            while not self.store.claim(self.provider,self.token,self.capacity,self.interval,self.ttl):
                if time.monotonic()>=deadline:raise ProviderBusy('外部服务请求较多，请稍后重试')
                time.sleep(.025)
            self.thread=threading.Thread(target=self.heartbeat,daemon=True)
            try:self.thread.start()
            except BaseException:self.store.release(self.token);raise
        except (OSError,sqlite3.Error) as error:
            raise ProviderBusy('外部服务额度暂不可用，请稍后重试') from error

    def heartbeat(self):
        while not self.stop.wait(self.ttl/3):
            try:
                if not self.store.renew(self.token,self.ttl):self.lost.set();return
            except (OSError,sqlite3.Error):self.lost.set();return

    def ensure(self):
        if self.lost.is_set():raise ProviderBusy('外部服务额度连接中断，请稍后重试')

    def release(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=1)
        try:self.store.release(self.token)
        except (OSError,sqlite3.Error) as error:raise ProviderBusy('外部服务额度暂不可用，请稍后重试') from error


def make_lease(provider):
    if provider not in ('amap','chat','ocr','qweather','variflight'):raise ValueError('Unknown provider group')
    from app.db.database import DATA_DIR
    capacity={'amap':2,'chat':2,'ocr':1,'qweather':2,'variflight':1}[provider]
    interval={'amap':.4,'chat':.4,'ocr':1,'qweather':.5,'variflight':1}[provider]
    try:
        capacity=int(os.environ.get(f'TRAVEL_{provider.upper()}_CONCURRENCY',capacity))
        interval=float(os.environ.get(f'TRAVEL_{provider.upper()}_INTERVAL',interval))
        if not 1<=capacity<=16 or not math.isfinite(interval) or not 0<=interval<=60:raise ValueError()
    except (ValueError,TypeError):
        raise ProviderBusy('外部服务额度配置无效，请联系管理员') from None
    return Lease(BudgetStore(DATA_DIR/'provider-budget.db'),provider,capacity,interval)


@contextmanager
def provider_slot(provider):
    if not enabled():yield;return
    lease=make_lease(provider);lease.acquire()
    try:yield;lease.ensure()
    finally:lease.release()


@asynccontextmanager
async def async_provider_slot(provider):
    if not enabled():yield;return
    lease=make_lease(provider)
    # Acquisition is bounded. On cancellation wait for it to finish and release
    # a potentially acquired slot rather than orphaning a heartbeat thread.
    task=asyncio.create_task(run_in_threadpool(lease.acquire))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        try:await task
        except BaseException:pass
        if lease.thread:await asyncio.shield(asyncio.to_thread(lease.release))
        raise
    try:yield;lease.ensure()
    finally:await asyncio.shield(asyncio.to_thread(lease.release))


def amap_get(*args,client=None,**kwargs):
    try:
        with provider_slot('amap'):
            return (client.get if client is not None else httpx.get)(*args,**kwargs)
    except ProviderBusy as error:
        # Existing map/weather callers already translate HTTP failures safely.
        raise httpx.RequestError(str(error)) from None
