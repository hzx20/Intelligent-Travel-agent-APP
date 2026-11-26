"""Temporary Linux nginx/backend rehearsal; never install or reload.

Run from a private uploaded QA directory below the authorized /tmp root. Ready
JSON is followed by a bounded stdin wait, so an SSH disconnect triggers cleanup.
Starting this runner requires explicit authorization for the isolated processes;
an earlier nginx syntax-check authorization alone does not cover its execution.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import select
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import uuid

from tools.proxy_chain_fixture import render_fixture


BACKEND_BOOTSTRAP = r'''import asyncio,pathlib,socket,sys
original=socket.socket.connect
original_ex=socket.socket.connect_ex
def local_only(sock,address):
    if sock.family in (socket.AF_INET,socket.AF_INET6) and address[0] not in ('127.0.0.1','::1'):raise RuntimeError('External calls forbidden in fixture')
    return original(sock,address)
socket.socket.connect=local_only
def local_only_ex(sock,address):
    if sock.family in (socket.AF_INET,socket.AF_INET6) and address[0] not in ('127.0.0.1','::1'):raise RuntimeError('External calls forbidden in fixture')
    return original_ex(sock,address)
socket.socket.connect_ex=local_only_ex
import app.main as entry
from fastapi import Request
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRoute
from app.core.config import ENV_FILE
from app.db.database import DATA_DIR
assert pathlib.Path(entry.__file__).resolve()==pathlib.Path(sys.argv[1])/'backend/app/main.py'
assert str(ENV_FILE)==sys.argv[5] and str(DATA_DIR)==sys.argv[6]
async def peer(request:Request):return {'client':request.client.host,'scheme':request.scope['scheme']}
async def stream():
    async def events():
        yield 'data: first\n\n'
        await asyncio.sleep(1)
        yield 'data: second\n\n'
    return StreamingResponse(events(),media_type='text/event-stream')
entry.app.router.routes.insert(0,APIRoute('/api/__fixture/client',peer,methods=['GET']))
entry.app.router.routes.insert(0,APIRoute('/api/plan/stream',stream,methods=['GET']))
import uvicorn
uvicorn.run(entry.app,host=sys.argv[2],port=int(sys.argv[3]),workers=1,proxy_headers=True,forwarded_allow_ips=sys.argv[4],log_level='warning')
'''


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def http(origin, route, body=None, headers=None, *, method=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(origin + route, data=data, method=method,
                                     headers={'Content-Type': 'application/json', **(headers or {})})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        response = opener.open(request, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, dict(response.headers), response.read(4 * 1024 * 1024)


def check_budget_review(origin, trip_id, auth):
    path = f'/travel/api/trips/{trip_id}'
    status, _, body = http(origin, path, headers=auth)
    assert status == 200
    trip = json.loads(body)
    assert trip['budget_review'] == {}
    quote = {'status': 'quoted', 'amount_minor': 98000, 'source': 'Synthetic accommodation',
             'checked_on': '2026-11-18'}
    edit = {'version': trip['version'], 'checklist': {'limit_minor': 300000, 'entries': {'lodging': quote}}}
    status, _, body = http(origin, path + '/budget-review', edit, auth, method='PATCH')
    assert status == 200 and json.loads(body)['budget_review']['checklist']['entries']['lodging'] == quote
    status, _, body = http(origin, path, headers=auth)
    assert status == 200 and json.loads(body)['budget_review']['checklist']['entries']['lodging'] == quote
    assert http(origin, path + '/budget-review', edit, auth, method='PATCH')[0] == 409
    status, _, body = http(origin, path + '/expenses', headers=auth)
    assert status == 200 and json.loads(body)['items'] == []


def high_port(address):
    for _ in range(20):
        with socket.socket() as sock:
            sock.bind((address, 0))
            port = sock.getsockname()[1]
        if port >= 20000:
            return port
    raise RuntimeError('No safe high port selected')


def run(args):
    base, candidate, work, merged = (Path(p).absolute() for p in (args.base, args.candidate, args.workdir, args.merged))
    if not re.fullmatch(r'/tmp/travel-release-rehearsal-\d{8}-[0-9a-f]{8}', str(base)):
        raise ValueError('Unapproved temporary namespace')
    if any(p != p.resolve() or not p.is_relative_to(base) for p in (base, candidate, work, merged)):
        raise ValueError('Paths must remain canonical inside private rehearsal root')
    if work.exists() or base.stat().st_uid != os.getuid() or base.stat().st_mode & 0o077 or args.container != 'continew-web':
        raise ValueError('Work must be new and container must be the reviewed target')
    overlay = None if args.no_dependency_overlay else base / 'deps'
    if overlay is not None and (overlay.resolve() != overlay or not overlay.is_dir() or overlay.stat().st_uid != os.getuid()):
        raise ValueError('Expected the existing owned dependency overlay')
    os.umask(0o077)
    work.mkdir(mode=0o700)
    sys.path.insert(0, str(candidate / 'backend'))
    from tools.release_candidate import verify
    from tools.frontend_release import verify as verify_frontend
    metadata = verify(candidate)
    if metadata['id'] != args.expected_release_id:
        raise ValueError('Unexpected code release')
    front = verify_frontend(candidate / 'frontend')
    if hashlib.sha256(merged.read_bytes()).hexdigest() != args.expected_merged_sha256:
        raise ValueError('Reviewed merged configuration changed')
    source = Path('/opt/continew/nginx.conf')
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    if source_hash != args.expected_source_sha256:
        raise ValueError('Production source configuration drifted')
    service_pid = subprocess.check_output(['systemctl', 'show', 'travel', '--property=MainPID', '--value'], text=True).strip()
    docker = ['sudo', '-n', 'docker', 'exec', args.container]
    container_source_hash = subprocess.check_output([*docker, 'sha256sum', '/etc/nginx/nginx.conf'], text=True).split()[0]
    if container_source_hash != source_hash:
        raise ValueError('Host/container production configuration differ')
    networks = json.loads(subprocess.check_output(['sudo', '-n', 'docker', 'inspect', args.container, '--format', '{{json .NetworkSettings.Networks}}'], text=True))
    if len(networks) != 1:
        raise ValueError('Multiple container networks require review')
    network = next(iter(networks.values()))
    if network['IPAddress'] != args.nginx_ip or network['Gateway'] != args.gateway:
        raise ValueError('Container network changed')
    nginx_master = subprocess.check_output([*docker, 'cat', '/var/run/nginx.pid'], text=True).strip()
    available_kb = int(subprocess.check_output([*docker, 'df', '-Pk', '/dev/shm'], text=True).splitlines()[-1].split()[3])
    if available_kb < 12000:
        raise RuntimeError('Insufficient container memory-filesystem space')
    memory_available = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
    if memory_available < 350 * 1024:
        raise RuntimeError('Insufficient host memory for isolated backend')
    backend_port = high_port(args.gateway)
    # The selected container port is not published to the host or public network.
    nginx_port = 40000 + (int(uuid.uuid4().hex[:4], 16) % 20000)
    memory = '/dev/shm/travel-proxy-chain-' + uuid.uuid4().hex
    fixture = render_fixture(merged.read_text(encoding='utf-8'), memory, args.nginx_ip, nginx_port, args.gateway, backend_port)
    data = work / 'data'
    secret = work / 'synthetic.env'
    secret.write_text('JWT_SECRET=' + secrets.token_hex(32) + '\n', encoding='utf-8')
    env = {'PATH': '/usr/bin:/bin', 'TMPDIR': str(work), 'PYTHONUTF8': '1'}
    env.update({'TRAVEL_DATA_DIR': str(data), 'TRAVEL_ENV_FILE': str(secret), 'PYTHONDONTWRITEBYTECODE': '1',
                'PYTHONNOUSERSITE': '1',
                'AMAP_API_KEY': '', 'AMAP_JS_KEY': '', 'AMAP_JS_SECURITY_CODE': '', 'ZHIPU_API_KEY': '',
                'TRAVEL_SHARED_LIMITS': '0', 'TRAVEL_SHARED_CACHE': '0', 'TRAVEL_PROVIDER_BUDGET': '0'})
    if overlay is not None:
        env['PYTHONPATH'] = str(overlay)

    backend = nginx = None
    memory_created = False
    report = {'release_id': metadata['id'], 'source_config_sha256': source_hash,
              'merged_config_sha256': hashlib.sha256(merged.read_bytes()).hexdigest(), 'production_deployed': False,
              'synthetic_data_only': True, 'published_ports_created': False,
              'dependency_overlay_used': overlay is not None, 'success': False}
    try:
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w') as tar:
            payload = {'nginx.conf': fixture.encode()}
            payload.update({'html/travel/' + name: (candidate / 'frontend' / name).read_bytes() for name in front['files']})
            payload['html/travel/.travel-deploy/probe.json'] = b'PRIVATE_SYNTHETIC_RECORD'
            for name, content in payload.items():
                item = tarfile.TarInfo(name); item.size = len(content); item.mode = 0o600
                tar.addfile(item, io.BytesIO(content))
        created = subprocess.run([*docker, 'sh', '-c', 'umask 077; mkdir -- "$1"', 'fixture', memory], capture_output=True, timeout=10)
        if created.returncode:
            raise RuntimeError('Could not exclusively create container fixture directory')
        memory_created = True
        prepared = subprocess.run([*docker, 'sh', '-c', 'mkdir "$1/temp" && ln -s /etc/nginx/mime.types "$1/mime.types"', 'fixture', memory], capture_output=True, timeout=10)
        if prepared.returncode:
            raise RuntimeError('Could not prepare exclusively owned container fixture directory')
        unpacked = subprocess.run(['sudo', '-n', 'docker', 'exec', '-i', args.container, 'tar', '-xf', '-', '-C', memory], input=archive.getvalue(), capture_output=True, timeout=10)
        if unpacked.returncode:
            raise RuntimeError('Container fixture extraction failed')
        checked = subprocess.run([*docker, 'nginx', '-p', memory + '/', '-c', memory + '/nginx.conf', '-t'], capture_output=True, text=True, timeout=10)
        report['fixture_syntax'] = {'exit_code': checked.returncode, 'output': checked.stdout + checked.stderr}
        if checked.returncode:
            raise RuntimeError('Fixture configuration did not pass syntax check')
        with (work / 'backend.log').open('ab') as log:
            backend = subprocess.Popen([sys.executable, '-B', '-c', BACKEND_BOOTSTRAP, str(candidate), args.gateway, str(backend_port), args.nginx_ip, str(secret), str(data)], cwd=candidate / 'backend', env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        origin = f'http://{args.nginx_ip}:{nginx_port}'
        direct = f'http://{args.gateway}:{backend_port}'
        deadline = time.monotonic() + 25
        while True:
            if backend.poll() is not None:
                raise RuntimeError('Isolated backend exited; inspect private backend.log')
            try:
                if http(direct, '/api/health')[0] == 200:
                    break
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError('Backend startup timed out')
            time.sleep(.1)
        with (work / 'nginx-client.log').open('ab') as log:
            nginx = subprocess.Popen([*docker, 'nginx', '-p', memory + '/', '-c', memory + '/nginx.conf'], stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        deadline = time.monotonic() + 15
        while True:
            if nginx.poll() is not None:
                raise RuntimeError('Isolated nginx exited; inspect private nginx-client.log')
            try:
                if http(origin, '/travel/api/health')[0] == 200:
                    break
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError('Nginx startup timed out')
            time.sleep(.1)
        root_status, _, root_body = http(origin, '/')
        original_index = Path('/opt/continew/web/index.html').read_bytes()
        assert root_status == 200 and root_body == original_index
        assert http(origin, '/travel')[0] == 301
        status, headers, body = http(origin, '/travel/trips/123?panel=assistant')
        assert status == 200 and hashlib.sha256(body).hexdigest() == front['files']['index.html']
        assert 'no-store' in headers.get('Cache-Control', '')
        assets = 0
        for name, expected in front['files'].items():
            if not name.startswith('assets/'):
                continue
            status, headers, body = http(origin, '/travel/' + name)
            assert status == 200 and hashlib.sha256(body).hexdigest() == expected
            assert 'immutable' in headers.get('Cache-Control', '')
            if name.endswith('.js'):
                assert 'javascript' in headers.get('Content-Type', '')
            assets += 1
        for route in ('/travel/assets/missing.js', '/travel/.vite/manifest.json', '/travel/.travel-deploy/probe.json'):
            assert http(origin, route)[0] == 404
        spoof = {'X-Forwarded-For': '203.0.113.29', 'X-Real-IP': '203.0.113.20', 'X-Forwarded-Proto': 'https'}
        proxied_peer = json.loads(http(origin, '/travel/api/__fixture/client', headers=spoof)[2])
        direct_peer = json.loads(http(direct, '/api/__fixture/client')[2])
        direct_spoofed_peer = json.loads(http(direct, '/api/__fixture/client', headers=spoof)[2])
        report['proxy_observations'] = {'proxied': proxied_peer, 'direct': direct_peer, 'direct_spoofed': direct_spoofed_peer}
        assert proxied_peer == {'client': args.gateway, 'scheme': 'http'}
        assert direct_peer['client'] != args.nginx_ip and direct_peer['scheme'] == 'http'
        assert direct_spoofed_peer == direct_peer
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(origin + '/travel/api/plan/stream', timeout=5) as response:
            assert response.readline().strip() == b'data: first'
            first = time.monotonic()
            while True:
                line = response.readline()
                if not line or line.startswith(b'data:'):
                    break
            assert line.strip() == b'data: second' and time.monotonic() - first >= .6
        statuses = [http(origin, '/travel/api/trips/guest-guide-preview', {})[0] for _ in range(6)]
        assert statuses[0] == 422 and 429 in statuses
        username = 'proxy_chain_' + uuid.uuid4().hex[:8]
        assert http(origin, '/travel/api/auth/register', {'username': username, 'password': 'synthetic-proxy-chain-password'})[0] == 201
        status, _, body = http(origin, '/travel/api/auth/login', {'username': username, 'password': 'synthetic-proxy-chain-password'})
        assert status == 200
        auth = {'Authorization': 'Bearer ' + json.loads(body)['token']}
        creation = {'title': 'Synthetic proxy chain', 'days': 1, 'request_id': '11111111-1111-4111-8111-111111111111'}
        status, _, body = http(origin, '/travel/api/trips/create', creation, auth); assert status == 201
        trip_id = json.loads(body)['id']
        status, _, body = http(origin, '/travel/api/trips/create', creation, auth)
        assert status == 200 and json.loads(body)['id'] == trip_id
        if args.check_budget_review:
            check_budget_review(origin, trip_id, auth)
        report['http'] = {'original_root_unchanged': True, 'deep_spa_and_no_store': True, 'asset_hashes_verified': assets,
                          'missing_and_private_paths_404': True, 'trusted_proxy_client_and_scheme': True,
                          'untrusted_direct_headers_ignored': True, 'synthetic_sse_unbuffered': True,
                          'ai_invalid_request_statuses': statuses, 'ordinary_creation_after_quota': True,
                          'budget_review_via_proxy': args.check_budget_review}
        print(json.dumps({'type': 'ready', 'nginx_ip': args.nginx_ip, 'nginx_port': nginx_port, 'backend_port': backend_port, 'http': report['http']}), flush=True)
        deadline = time.monotonic() + min(max(args.hold_seconds, 0), 120)
        while time.monotonic() < deadline:
            readable, _, _ = select.select([sys.stdin], [], [], 1)
            if readable:
                line = sys.stdin.readline()
                if not line or line.strip() == 'stop':
                    break
        report['success'] = True
    finally:
        # Complete cleanup if an SSH disconnect delivers another termination signal.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        signal.signal(signal.SIGALRM, signal.SIG_IGN)
        signal.alarm(0)
        cleanup_errors = []
        nginx_stopped = nginx is None
        if nginx is not None:
            try:
                if nginx.poll() is None:
                    subprocess.run([*docker, 'nginx', '-p', memory + '/', '-c', memory + '/nginx.conf', '-s', 'quit'], capture_output=True, timeout=10)
                try:
                    nginx.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    subprocess.run([*docker, 'nginx', '-p', memory + '/', '-c', memory + '/nginx.conf', '-s', 'stop'], capture_output=True, timeout=10)
                    nginx.wait(timeout=10)
                absent = subprocess.run([*docker, 'test', '!', '-e', memory + '/nginx.pid'], capture_output=True, timeout=5)
                nginx_stopped = nginx.poll() is not None and absent.returncode == 0
            except Exception as error:
                cleanup_errors.append('nginx: ' + type(error).__name__)
        backend_stopped = backend is None
        if backend is not None:
            try:
                if backend.poll() is None:
                    backend.terminate()
                try:
                    backend.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    backend.kill(); backend.wait(timeout=10)
                backend_stopped = backend.poll() is not None
            except Exception as error:
                cleanup_errors.append('backend: ' + type(error).__name__)
        report['own_processes_stopped'] = nginx_stopped and backend_stopped
        if memory_created and nginx_stopped:
            # The path was generated here, validated by render_fixture, and created
            # exclusively. Remove only this run's RAM tree, not any mounted source.
            try:
                deleted = subprocess.run([*docker, 'sh', '-c', 'test ! -L "$1" && rm -rf -- "$1" && test ! -e "$1"', 'fixture', memory], capture_output=True, timeout=10)
                report['container_ram_removed'] = deleted.returncode == 0
            except Exception as error:
                cleanup_errors.append('RAM: ' + type(error).__name__)
                report['container_ram_removed'] = False
        else:
            report['container_ram_removed'] = not memory_created
        if backend_stopped:
            assert data.resolve().parent == work
            if data.exists():
                shutil.rmtree(data)
            secret.unlink(missing_ok=True)
        report['synthetic_data_and_env_removed'] = not data.exists() and not secret.exists()
        report['cleanup_errors'] = cleanup_errors
        report['candidate_unchanged'] = verify(candidate) == metadata
        report['source_config_unchanged'] = hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
        report['container_source_config_unchanged'] = subprocess.check_output([*docker, 'sha256sum', '/etc/nginx/nginx.conf'], text=True).split()[0] == container_source_hash
        report['production_service_pid_unchanged'] = subprocess.check_output(['systemctl', 'show', 'travel', '--property=MainPID', '--value'], text=True).strip() == service_pid
        report['production_nginx_pid_unchanged'] = subprocess.check_output([*docker, 'cat', '/var/run/nginx.pid'], text=True).strip() == nginx_master
        (work / 'report.json').write_text(json.dumps(report, indent=2))
        print(json.dumps({'type': 'final', 'report': report}), flush=True)
    assert report['own_processes_stopped'] and report['container_ram_removed'] and report['synthetic_data_and_env_removed']
    assert report['candidate_unchanged'] and report['source_config_unchanged'] and report['container_source_config_unchanged'] and report['production_service_pid_unchanged'] and report['production_nginx_pid_unchanged']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('base', 'candidate', 'workdir', 'merged', 'expected-source-sha256', 'expected-merged-sha256', 'expected-release-id', 'nginx-ip', 'gateway'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--container', default='continew-web')
    parser.add_argument('--hold-seconds', type=int, default=0)
    parser.add_argument('--no-dependency-overlay', action='store_true')
    parser.add_argument('--check-budget-review', action='store_true')
    args = parser.parse_args()
    def interrupted(signum, frame):
        raise InterruptedError('Rehearsal interrupted; cleaning own processes')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    signal.signal(signal.SIGALRM, interrupted)
    # Leave time within the four-minute authorization for upload and cleanup.
    signal.alarm(150)
    run(args)


if __name__ == '__main__':
    main()
