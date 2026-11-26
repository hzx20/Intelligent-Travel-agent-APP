from contextlib import closing
import json
import socket
import sqlite3
import subprocess
import sys
import pytest
from app.core.shared_cache import SharedJSONCache,CacheUnavailable
from test_service_restart import running_backend


def valid(v):return isinstance(v,dict) and isinstance(v.get('n'),int)


def test_shared_expiry_does_not_extend_original_lifetime(tmp_path):
    now=[100];path=tmp_path/'cache.db';a=SharedJSONCache(path,clock=lambda:now[0]);b=SharedJSONCache(path,clock=lambda:now[0]);calls=[]
    def load():calls.append(1);return {'n':len(calls)}
    assert a.get_or_load(['route'],load,ttl=10,validate=valid)==({'n':1},False)
    now[0]=109
    assert b.get_or_load(['route'],load,ttl=10,validate=valid)==({'n':1},True)
    now[0]=110
    assert b.get_or_load(['route'],load,ttl=10,validate=valid)==({'n':2},False)
    assert len(calls)==2


def test_expired_worker_cannot_publish_over_new_result(tmp_path):
    now=[100];cache=SharedJSONCache(tmp_path/'cache.db',clock=lambda:now[0],lease_seconds=2)
    def old():
        now[0]=103
        assert cache.get_or_load('same',lambda:{'n':2},ttl=10,validate=valid)==({'n':2},False)
        return {'n':1}
    with pytest.raises(CacheUnavailable,match='expired'):
        cache.get_or_load('same',old,ttl=10,validate=valid)
    assert cache.get_or_load('same',lambda:{'n':3},ttl=10,validate=valid)==({'n':2},True)


def test_failures_empty_and_invalid_values_are_not_cached(tmp_path):
    cache=SharedJSONCache(tmp_path/'cache.db')
    def fail():raise RuntimeError('provider down')
    with pytest.raises(RuntimeError):cache.get_or_load('k',fail,ttl=10,validate=valid)
    with pytest.raises(CacheUnavailable):cache.get_or_load('k',lambda:None,ttl=10,validate=valid)
    assert cache.get_or_load('k',lambda:{'n':0},ttl=10,validate=valid,cacheable=lambda v:False)==({'n':0},False)
    assert cache.get_or_load('k',lambda:{'n':1},ttl=10,validate=valid)==({'n':1},False)
    with sqlite3.connect(cache.path) as db:
        assert db.execute('SELECT COUNT(*) FROM leases').fetchone()[0]==0
        db.execute("UPDATE entries SET value='not json'");db.commit()
    assert cache.get_or_load('k',lambda:{'n':2},ttl=10,validate=valid)==({'n':2},False)


def test_busy_capacity_and_broken_database_do_not_bypass_cache(tmp_path):
    path=tmp_path/'cache.db';cache=SharedJSONCache(path,max_entries=1,wait_seconds=.05)
    cache.get_or_load('first',lambda:{'n':1},ttl=30,validate=valid)
    def unexpected():
        pytest.fail('An already full cache must reject a miss before loading')
    assert cache.get_or_load('first',unexpected,ttl=30,validate=valid)==({'n':1},True)
    with pytest.raises(CacheUnavailable,match='capacity'):
        cache.get_or_load('second',unexpected,ttl=30,validate=valid)
    path=tmp_path/'broken.db';path.write_bytes(b'broken')
    with pytest.raises(CacheUnavailable):SharedJSONCache(path).get_or_load('x',lambda:{'n':1},ttl=30,validate=valid)


def test_inflight_load_reserves_last_entry_until_it_finishes(tmp_path):
    cache = SharedJSONCache(tmp_path/'cache.db', max_entries=1)
    other = SharedJSONCache(cache.path, max_entries=1)

    def unexpected():
        pytest.fail('A second miss must not load through the reserved slot')

    def first():
        with pytest.raises(CacheUnavailable, match='capacity'):
            other.get_or_load('second', unexpected, ttl=30, validate=valid)
        return {'n': 1}

    assert cache.get_or_load('first', first, ttl=30, validate=valid, cacheable=lambda value: False) == ({'n': 1}, False)
    assert other.get_or_load('second', lambda: {'n': 2}, ttl=30, validate=valid) == ({'n': 2}, False)


def test_live_hit_succeeds_while_another_connection_holds_write_reservation(tmp_path):
    cache = SharedJSONCache(tmp_path/'cache.db')
    assert cache.get_or_load('warm', lambda: {'n': 1}, ttl=30, validate=valid) == ({'n': 1}, False)

    def unexpected():
        pytest.fail('A cache hit must not reload')

    with closing(sqlite3.connect(cache.path)) as writer:
        writer.execute('BEGIN IMMEDIATE')
        assert cache.get_or_load('warm', unexpected, ttl=30, validate=valid) == ({'n': 1}, True)


def test_byte_limit_rejects_at_publish_and_releases_lease(tmp_path):
    cache = SharedJSONCache(tmp_path/'cache.db', max_bytes=14)
    assert cache.get_or_load('first', lambda: {'n': 1}, ttl=30, validate=valid) == ({'n': 1}, False)
    with pytest.raises(CacheUnavailable, match='capacity'):
        cache.get_or_load('second', lambda: {'n': 2}, ttl=30, validate=valid)
    with closing(cache.connect()) as db:
        assert db.execute('SELECT count(*) FROM entries').fetchone() == (1,)
        assert db.execute('SELECT count(*) FROM leases').fetchone() == (0,)


def test_retries_locked_publish_without_loading_again(tmp_path, monkeypatch):
    cache = SharedJSONCache(tmp_path/'cache.db')
    calls = []
    attempts = []
    original = cache._publish

    def load():
        calls.append(1)
        return {'n': 1}

    def locked_at_first(*args):
        attempts.append(1)
        if len(attempts) == 1:
            error = sqlite3.OperationalError('database is locked')
            error.sqlite_errorcode = sqlite3.SQLITE_BUSY
            raise error
        return original(*args)

    monkeypatch.setattr(cache,'_publish',locked_at_first)
    assert cache.get_or_load('key',load,ttl=30,validate=valid) == ({'n': 1},False)
    assert cache.get_or_load('key',load,ttl=30,validate=valid) == ({'n': 1},True)
    assert len(calls) == 1 and len(attempts) == 2


def test_five_processes_single_loader(tmp_path):
    path=tmp_path/'cache.db';counter=tmp_path/'calls.txt'
    code=r"""import sys,time,json
from pathlib import Path
from app.core.shared_cache import SharedJSONCache
def load():
 with open(sys.argv[2],'a') as f:f.write('call\n')
 time.sleep(.3)
 return {'n':1}
print(json.dumps(SharedJSONCache(sys.argv[1]).get_or_load('same',load,ttl=30,validate=lambda v:isinstance(v,dict))))
"""
    ps=[subprocess.Popen([sys.executable,'-c',code,str(path),str(counter)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) for _ in range(5)]
    values=[]
    try:
        for p in ps:
            out,err=p.communicate(timeout=20);assert p.returncode==0,err;values.append(json.loads(out))
    finally:
        for p in ps:
            if p.poll() is None:p.kill();p.wait()
    assert counter.read_text().splitlines()==['call']
    assert sum(not v[1] for v in values)==1


def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]


def test_real_servers_share_route_and_hotels_and_restart(tmp_path):
    log=tmp_path/'provider-calls.txt'
    bootstrap=r"""import os,sys
os.environ['TRAVEL_SHARED_CACHE']='1'
from app.core.config import settings
settings.amap_api_key='test-only-key'
import httpx
def fake(url,params=None,timeout=None):
 with open(sys.argv[2],'a') as f:f.write(url+'\n')
 payload={'status':'1','route':{'paths':[{'distance':'123','cost':{'duration':'60'},'steps':[]}]}} if 'direction' in url else {'status':'1','pois':[{'name':'Synthetic hotel','location':'120,30','photos':[],'business':{}}]}
 return httpx.Response(200,request=httpx.Request('GET',url),json=payload)
httpx.get=fake
import uvicorn
uvicorn.run('app.main:app',host='127.0.0.1',port=int(sys.argv[1]),log_level='warning',proxy_headers=False)
"""
    data=tmp_path/'data';args=(str(log),)
    route={'origin':{'lng':120,'lat':30},'destination':{'lng':121,'lat':31}}
    with running_backend(data,port(),bootstrap=bootstrap,bootstrap_args=args) as a:
        with running_backend(data,port(),bootstrap=bootstrap,bootstrap_args=args) as b:
            first=a.post('/api/map/guest-route',json=route).json();assert first['cached'] is False
            again=b.post('/api/map/guest-route',json=route).json();assert again['cached'] is True and again['queried_at']==first['queried_at']
            h=a.get('/api/hotels',params={'lng':120,'lat':30,'city':'杭州'}).json()
            assert b.get('/api/hotels',params={'lng':120,'lat':30,'city':'杭州'}).json()==h
            assert len(log.read_text().splitlines())==2
            other=b.get('/api/hotels',params={'lng':120,'lat':30,'city':'上海'}).json()
            assert other['items'][0]['links']!=h['items'][0]['links']
            assert len(log.read_text().splitlines())==3
    with running_backend(data,port(),bootstrap=bootstrap,bootstrap_args=args) as c:
        assert c.post('/api/map/guest-route',json=route).json()['cached'] is True
        assert len(log.read_text().splitlines())==3


@pytest.mark.parametrize('writer_reserved', [False, True])
def test_busy_lease_times_out_without_another_provider_call(tmp_path, writer_reserved):
    import hashlib
    import time

    cache = SharedJSONCache(tmp_path / 'busy.db', wait_seconds=.05)
    key = hashlib.sha256(json.dumps('busy').encode()).hexdigest()
    calls = []
    with closing(cache.connect()) as writer:
        writer.execute('INSERT INTO leases VALUES(?,?,?)', (key, 'other-worker', time.time() + 60))
        if writer_reserved:
            writer.execute('BEGIN IMMEDIATE')
        with pytest.raises(CacheUnavailable, match='still running'):
            cache.get_or_load('busy', lambda: calls.append(1), ttl=30, validate=valid)
    assert not calls


def test_real_servers_share_qweather_forecast_and_reuse_it_after_restart(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from test_travel_suppliers import forecast

    counter = tmp_path / 'weather-requests.txt'
    daily = tmp_path / 'forecast.json'
    daily.write_text(json.dumps(forecast()), encoding='ascii')
    (tmp_path / 'fixture.env').touch()
    bootstrap = r"""
import json
import os
from pathlib import Path
import sys
from time import sleep
from types import SimpleNamespace
import httpx
import uvicorn

os.environ['TRAVEL_ENV_FILE'] = str(Path(sys.argv[2]).parent / 'fixture.env')
os.environ['TRAVEL_SHARED_CACHE'] = '1'
os.environ.pop('TRAVEL_PROVIDER_BUDGET', None)
os.environ['QWEATHER_API_HOST'] = 'fixture.qweatherapi.com'
os.environ['QWEATHER_API_KEY'] = 'fixture-key'

from app.services import qweather

def mocked(request):
    path = request.url.path
    with open(sys.argv[2], 'a', encoding='ascii') as log:
        log.write(path + '\n')
    if path == '/geo/v2/city/lookup':
        sleep(.25)
        return httpx.Response(200, json={
            'code': '200', 'location': [{'name': '\u676d\u5dde', 'lat': '30.27', 'lon': '120.16'}],
        })
    return httpx.Response(200, json=json.loads(Path(sys.argv[3]).read_text(encoding='ascii')))

def weather_client(**kwargs):
    return httpx.Client(transport=httpx.MockTransport(mocked), **kwargs)

qweather.httpx = SimpleNamespace(Timeout=httpx.Timeout, Client=weather_client)
uvicorn.run('app.main:app', host='127.0.0.1', port=int(sys.argv[1]), log_level='warning')
"""
    data = tmp_path / 'data'
    args = (str(counter), str(daily))
    query = ('/api/travel-data/weather', {'city': '杭州'})
    with running_backend(data, port(), bootstrap=bootstrap, bootstrap_args=args) as first:
        with running_backend(data, port(), bootstrap=bootstrap, bootstrap_args=args) as second:
            account = {'username': 'weather_cache_fixture', 'password': 'test-password-only'}
            assert first.post('/api/auth/register', json=account).status_code == 201
            login = first.post('/api/auth/login', json=account)
            assert login.status_code == 200
            auth = {'Authorization': f"Bearer {login.json()['token']}"}

            def lookup(client):
                response = client.get(query[0], params=query[1], headers=auth)
                assert response.status_code == 200
                return response.json()

            with ThreadPoolExecutor(2) as pool:
                results = list(pool.map(lookup, (first, second)))
            assert results[0] == results[1]
            assert results[0]['casts'][0]['date'] == '2026-11-17'
    with running_backend(data, port(), bootstrap=bootstrap, bootstrap_args=args) as restarted:
        assert lookup(restarted) == results[0]
    assert counter.read_text().splitlines() == ['/geo/v2/city/lookup', '/weather/v1/daily/30.27/120.16']


def test_services_report_broken_shared_store_without_provider_fallback(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from app.core import shared_cache
    from app.services import trip_routes
    from app.routers import hotels
    path=tmp_path/'broken.db';path.write_bytes(b'broken')
    monkeypatch.setattr(shared_cache,'provider_cache',lambda:SharedJSONCache(path))
    calls=[]
    def unexpected(*args,**kwargs):calls.append(1);raise AssertionError('must not call provider')
    monkeypatch.setattr(trip_routes.httpx,'get',unexpected)
    origin=SimpleNamespace(lng=120,lat=30,amap_id='')
    with pytest.raises(trip_routes.RouteUnavailable,match='缓存暂不可用'):
        trip_routes.get_route(origin,origin,'walking')
    result=hotels.nearby_hotels(120,30,'杭州',3000)
    assert result['ok'] is False and result['items']==[] and '缓存暂不可用' in result['note']
    assert not calls
