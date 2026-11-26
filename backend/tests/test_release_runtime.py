"""Actual optional nginx + two Uvicorn processes with all shared controls on."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import ssl
import subprocess
import time

import httpx
import pytest
from test_service_restart import running_backend
from tools.frontend_release import prepare, publish, rollback, status

ROOT=Path(__file__).resolve().parents[2]
NGINX=Path(os.environ.get('TRAVEL_TEST_NGINX_EXE',ROOT/'.tools/nginx-1.30.6/nginx.exe'))
DIST=ROOT/'frontend/dist'
pytestmark=pytest.mark.skipif(not NGINX.is_file() or not (DIST/'.vite/manifest.json').is_file(),reason='Explicit nginx binary and production build required')


def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]


@contextmanager
def nginx_server(prefix,backend_port, *, certificate=None, key=None):
    prefix.mkdir();(prefix/'logs').mkdir();(prefix/'html').mkdir();(prefix/'temp').mkdir()
    shutil.copytree(DIST,prefix/'html/travel')
    text=(ROOT/'deploy/nginx-travel.conf').read_text(encoding='utf-8')
    before,locations=text.split('# ---- 放在 server 块内',1)
    # Keep the complete production http-map/limit and location blocks; substitute
    # only upstream/listener/root and the fixture's unrelated root location.
    before=before.split('# ---- 放在 http 块内',1)[1].split('\n',1)[1]
    locations=locations[locations.index('\n'):].replace('http://172.18.0.1:8000/',f'http://127.0.0.1:{backend_port}/')
    listen=port()
    if (certificate is None)!=(key is None):
        raise ValueError('TLS needs both a certificate and key')
    tls = (f'ssl_certificate "{certificate.as_posix()}"; ssl_certificate_key "{key.as_posix()}"; '
           'ssl_protocols TLSv1.2 TLSv1.3;') if certificate else ''
    listener = f'127.0.0.1:{listen}' + (' ssl' if tls else '')
    conf=f'''worker_processes 1;
daemon off;
master_process off;
pid logs/nginx.pid;
error_log logs/error.log;
events {{ worker_connections 128; }}
http {{
 include "{(NGINX.parent/'conf/mime.types').as_posix()}";
 default_type application/octet-stream;
 {before}
 server {{ listen {listener}; {tls} root html; index index.html;
 location = / {{ return 200 "OTHER-SITE"; }}
 {locations}
 }}
}}
'''
    (prefix/'nginx.conf').write_text(conf,encoding='utf-8')
    args=[str(NGINX),'-p',prefix.as_posix()+'/', '-c','nginx.conf']
    checked=subprocess.run([*args,'-t'],capture_output=True,text=True,timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    assert checked.returncode==0,checked.stdout+checked.stderr
    process=subprocess.Popen(args,cwd=prefix,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    client=httpx.Client(base_url=f'{"https" if tls else "http"}://127.0.0.1:{listen}',trust_env=False,timeout=5,
                        verify=ssl.create_default_context(cafile=str(certificate)) if tls else True)
    try:
        for _ in range(100):
            assert process.poll() is None,(prefix/'logs/error.log').read_text(errors='replace')
            try:
                if client.get('/').status_code==200:break
            except httpx.TransportError:pass
            time.sleep(.05)
        else:raise AssertionError('nginx startup timed out')
        yield client
    finally:
        client.close()
        try:
            if tls and os.name == 'nt':
                process.terminate()  # Single, non-daemon fixture process; pending TLS keepalive can delay quit.
            else:
                subprocess.run([*args,'-s','quit'],capture_output=True,timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        finally:
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)


def test_real_proxy_bundle_and_three_control_combination(tmp_path):
    calls=tmp_path/'provider-calls.txt'
    bootstrap=r'''import os,sys
for flag in ('TRAVEL_SHARED_LIMITS','TRAVEL_SHARED_CACHE','TRAVEL_PROVIDER_BUDGET'):os.environ[flag]='1'
from app.core.config import settings
settings.amap_api_key='test-only-key'
import httpx
def fake(url,params=None,timeout=None):
 with open(sys.argv[2],'a') as f:f.write(url+'\n')
 return httpx.Response(200,request=httpx.Request('GET',url),json={'status':'1','route':{'paths':[{'distance':'100','cost':{'duration':'60'},'steps':[]}]}})
httpx.get=fake
import uvicorn
uvicorn.run('app.main:app',host='127.0.0.1',port=int(sys.argv[1]),log_level='warning',proxy_headers=False)
'''
    data=tmp_path/'data';first_port=port();route={'origin':{'lng':120,'lat':30},'destination':{'lng':121,'lat':31}}
    with running_backend(data,first_port,bootstrap=bootstrap,bootstrap_args=(str(calls),)) as a:
        with running_backend(data,port(),bootstrap=bootstrap,bootstrap_args=(str(calls),)) as b:
            one=a.post('/api/map/guest-route',json=route).json()
            two=b.post('/api/map/guest-route',json=route).json()
            assert one['cached'] is False and two['cached'] is True and one['queried_at']==two['queried_at']
            assert len(calls.read_text().splitlines())==1
            with sqlite3.connect(data/'provider-budget.db') as db:
                assert db.execute('SELECT COUNT(*) FROM slots').fetchone()[0]==0
                assert db.execute('SELECT provider FROM starts').fetchall()==[('amap',)]
            for index in range(28):assert (a if index%2 else b).post('/api/map/guest-route',json=route).status_code==200
            assert a.post('/api/map/guest-route',json=route).status_code==429
            assert len(calls.read_text().splitlines())==1
            with nginx_server(tmp_path/'nginx',first_port) as proxy:
                assert proxy.get('/').text=='OTHER-SITE'
                assert proxy.get('/travel',follow_redirects=False).status_code==301
                home=proxy.get('/travel/');deep=proxy.get('/travel/trips/123?panel=assistant')
                assert home.status_code==deep.status_code==200 and home.content==deep.content
                assert 'no-store' in deep.headers.get('cache-control','')
                missing=proxy.get('/travel/assets/missing.js')
                assert missing.status_code==404 and '旅行规划平台' not in missing.text
                assert proxy.get('/travel/.vite/manifest.json').status_code==404
                manifest=json.loads((DIST/'.vite/manifest.json').read_text(encoding='utf-8'))
                for item in manifest.values():
                    response=proxy.get('/travel/'+item['file'])
                    assert response.status_code==200
                    if item['file'].endswith('.js'):assert 'javascript' in response.headers['content-type']
                    assert 'immutable' in response.headers.get('cache-control','')
                assert proxy.get('/travel/api/health').json()=={'status':'ok'}
                assert proxy.get('/travel/api/does-not-exist').status_code==404
                assert 'application/json' in proxy.get('/travel/api/does-not-exist').headers['content-type']
                # Ordinary account writes stay available after map quota is spent.
                register=proxy.post('/travel/api/auth/register',json={'username':'combined_controls','password':'synthetic-password'})
                assert register.status_code==201
                if os.environ.get('TRAVEL_E2E_PRODUCTION_SMOKE')=='1':
                    output=ROOT/'frontend/playwright-results/production-bundle'
                    result=subprocess.run(['node',str(ROOT/'frontend/e2e/production-smoke.mjs'),str(proxy.base_url).rstrip('/'),str(output)],cwd=ROOT/'frontend',capture_output=True,text=True,encoding='utf-8',timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    assert result.returncode==0,result.stdout+result.stderr
                    report=json.loads(result.stdout);assert len(report['production_bundle_smoke'])==2
                    output.mkdir(parents=True,exist_ok=True)
                    (output/'report.json').write_text(json.dumps(report),encoding='utf-8')


def test_real_nginx_frontend_publication_and_rollback(tmp_path):
    source=tmp_path/'new-dist'
    shutil.copytree(DIST,source)
    original=(source/'index.html').read_bytes()
    changed=original+b'\n<!-- release-switch-test -->\n'
    (source/'index.html').write_bytes(changed)
    candidate=tmp_path/'candidate'
    metadata=prepare(source,candidate)
    with nginx_server(tmp_path/'nginx',9) as proxy:
        target=tmp_path/'nginx/html/travel'
        assert proxy.get('/travel/').content==original
        publish(candidate,target)
        assert proxy.get('/travel/trips/123').content==changed
        assert 'no-store' in proxy.get('/travel/index.html').headers.get('cache-control','')
        assert status(target,metadata['id'])['matches_published']
        assert (target/'.travel-deploy'/f"{metadata['id']}.json").is_file()
        assert proxy.get(f"/travel/.travel-deploy/{metadata['id']}.json").status_code==404
        assert proxy.get('/travel/.travel-deploy/publish.lock').status_code==404
        rollback(target,metadata['id'])
        assert proxy.get('/travel/trips/123').content==original
        assert status(target,metadata['id'])['matches_previous']
        for name in metadata['files']:
            if name.startswith('assets/'):
                response=proxy.get('/travel/'+name)
                assert response.status_code==200
                assert 'immutable' in response.headers.get('cache-control','')
