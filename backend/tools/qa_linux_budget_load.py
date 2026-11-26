"""Bounded synthetic load on the fixed Linux release; requires a private systemd unit."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid


ROOT = Path('/mnt/travel-budget-load-input')  # read-only inside the transient service, not a host path
STAGE = ROOT / 'budget-38ba927c4ae4'
RUNTIME = ROOT / 'runtime-env-20261117-review1/venv'
HARNESS_SHA = '177b79a770c23e1d222ddd51469d3d6356195626289020132153e664e00a1996'
ROUTER = 'backend/app/routers/trips.py'
PATCHED_SHA = 'b1384ad3dce113095cefa39b7b18aa2e18122d29ff96464db56c191a4c96a558'
BEFORE = b'def get_trip(trip_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):'
AFTER = (b'def get_trip(trip_id: int, response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user)):'
         b'\r\n    response.headers[\'Cache-Control\'] = \'private, no-store\'')
READ_COUNT = 160
WORKERS = 8
MEM_MAX = 384 * 1024 * 1024
PHASE = 'isolation_guard'


BOOT = r'''import asyncio,json,pathlib,socket,sys
from pathlib import Path
root,work=map(Path,sys.argv[1:])
sys.path.insert(0,str(root/'backend'))
def forbid_external(event,args):
    if event=='socket.getaddrinfo' and str(args[0]) not in ('127.0.0.1','::1', 'None'):
        external_attempt()
    if event=='socket.connect' and args[0].family in (socket.AF_INET,socket.AF_INET6) and args[1][0] not in ('127.0.0.1','::1'):
        external_attempt()
def external_attempt():
    (work/'outbound-attempted').touch()
    raise RuntimeError('External calls not permitted')
sys.addaudithook(forbid_external)
from app import main
from app.core.config import ENV_FILE,settings
from app.db.database import DATA_DIR
from app.services import clarify
assert Path(main.__file__).resolve()==root/'backend/app/main.py'
assert DATA_DIR==work/'data' and ENV_FILE==work/'synthetic.env'
assert not any(getattr(settings,k,'') for k in ('zhipu_api_key','amap_api_key','qweather_api_key','variflight_api_key'))
async def fake_chat(*args,**kwargs):
    with (work/'synthetic-chat-count').open('a') as h: h.write('1\n')
    await asyncio.sleep(.65)
    return json.dumps({'collected':{'destination':'Chengdu'},'missing':['days'],'question':'How many days?'})
clarify.chat=fake_chat
import uvicorn
with socket.socket() as sock:
    sock.bind(('127.0.0.1',0))
    sock.listen(64)
    port=sock.getsockname()[1]
    (work/'ready.tmp').write_text(str(port))
    (work/'ready.tmp').rename(work/'ready')
    cfg=uvicorn.Config(main.app,workers=1,proxy_headers=False,log_level='warning',timeout_graceful_shutdown=3)
    uvicorn.Server(cfg).run(sockets=[sock])
'''


def demand(ok):
    if not ok:
        raise RuntimeError('Rehearsal check failed')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def patch_router(data):
    demand(data.count(BEFORE) == 1)
    updated = data.replace(BEFORE, AFTER, 1)
    demand(sha(updated) == PATCHED_SHA)
    return updated


def check_isolation(unit):
    demand(re.fullmatch('travel-budget-load-[a-f0-9]{16}\\.service', unit))
    demand(os.getuid() != 0 and platform.python_version() == '3.12.3' and platform.machine() == 'x86_64' and
           Path(sys.prefix) == RUNTIME and sys.flags.isolated and sys.dont_write_bytecode)
    demand([name for _, name in socket.if_nameindex()] == ['lo'])
    mount = [line.split() for line in Path('/proc/self/mountinfo').read_text().splitlines()]
    demand(all(any(e[4] == str(p) and 'ro' in e[5].split(',') for e in mount) for p in (STAGE, RUNTIME.parent)))
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    demand(status['NoNewPrivs'].strip() == '1')
    groups = Path('/proc/self/cgroup').read_text().splitlines()
    demand(len(groups) == 1 and groups[0] == '0::/system.slice/' + unit)
    group = Path('/sys/fs/cgroup/system.slice') / unit
    cpu, period = map(int, (group / 'cpu.max').read_text().split())
    demand(cpu / period == .5 and (group / 'memory.max').read_text().strip() == str(MEM_MAX) and
           (group / 'memory.swap.max').read_text().strip() == '0' and (group / 'pids.max').read_text().strip() == '64')
    return group


def metrics(group):
    def fields(name):
        return {k: int(v) for k, v in (line.split() for line in (group / name).read_text().splitlines())}
    cpu, memory = fields('cpu.stat'), fields('memory.events')
    return {'usage_usec': cpu['usage_usec'], 'nr_throttled': cpu['nr_throttled'],
            'oom': memory['oom'], 'oom_kill': memory['oom_kill'], 'peak_bytes': int((group / 'memory.peak').read_text())}


def workload(client):
    entries = []
    for index in range(2):
        account = {'username': f'synthetic_load_{index}', 'password': secrets.token_hex(12)}
        demand(client.post('/api/auth/register', json=account).status_code == 201)
        login = client.post('/api/auth/login', json=account)
        demand(login.status_code == 200)
        auth = {'Authorization': 'Bearer ' + login.json()['token']}
        response = client.post('/api/trips/create', headers=auth, json={
            'title': 'Synthetic load trip', 'days': 1, 'request_id': str(uuid.uuid4())})
        demand(response.status_code == 201)
        trip = response.json()
        entries.append((f'/api/trips/{trip["id"]}', auth, trip['version']))
    path, auth, version = entries[0]
    demand(client.get(path).status_code == 401 and client.get(path, headers=entries[1][1]).status_code == 404)
    stream_entered = threading.Event()

    def stream():
        began = time.monotonic()
        seen = []
        stream_entered.set()
        with client.stream('POST', '/api/plan/stream', json={'user_input': 'Synthetic destination'}) as resp:
            demand(resp.status_code == 200 and resp.headers.get('content-type', '').startswith('text/event-stream'))
            for line in resp.iter_lines():
                if line.startswith('event: '):
                    seen.append((line[7:], time.monotonic() - began))
                if line.startswith('data: ') and seen[-1][0] == 'result':
                    demand(json.loads(line[6:])['question'] == 'How many days?')
        demand([name for name, _ in seen][-2:] == ['result', 'done'] and seen[0][0] in ('token', 'log'))
        return {'first_ms': round(seen[0][1] * 1000, 2), 'done_ms': round(seen[-1][1] * 1000, 2), 'events': len(seen)}

    def read(index):
        a_path, a_auth, _ = entries[(index // 4) % len(entries)]
        q = index % 4
        start = time.monotonic()
        route = ('/api/health', '/api/trips', a_path, a_path + '/expenses')[q]
        response = client.get(route, headers=a_auth if q else {})
        demand(response.status_code == 200)
        if q == 2:
            demand(response.headers.get('cache-control') == 'private, no-store' and str(response.json()['id']) == a_path.rsplit('/', 1)[-1])
        if q == 0:
            demand(response.json() == {'status': 'ok'})
        return (time.monotonic() - start) * 1000

    edit = {'version': version, 'checklist': {'limit_minor': 100000, 'entries': {'lodging': {
        'status': 'quoted', 'amount_minor': 21500, 'source': 'Synthetic lodging', 'checked_on': '2026-11-19'}}}}
    barrier = threading.Barrier(2, timeout=4)

    def write():
        barrier.wait()
        return client.patch(path+'/budget-review', headers=auth, json=edit).status_code

    began = time.monotonic()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        sse = pool.submit(stream)
        # The request starts before the reads; both share the eight client slots.
        demand(stream_entered.wait(3))
        latencies = sorted(pool.map(read, range(READ_COUNT)))
        states = sorted(f.result() for f in [pool.submit(write), pool.submit(write)])
        event_result = sse.result()
    demand(states == [200, 409])
    saved = client.get(path, headers=auth)
    demand(saved.json()['version'] == version + 1 and saved.json()['budget_review']['checklist']['entries']['lodging'] ==
           edit['checklist']['entries']['lodging'])
    demand(client.get(path + '/expenses', headers=auth).json()['items'] == [])
    return {'read_requests': len(latencies), 'read_errors': 0, 'max_parallel_clients': WORKERS,
            'p95_ms': round(latencies[math.ceil(.95 * len(latencies)) - 1], 2), 'max_ms': round(latencies[-1], 2),
            'write_statuses': states, 'seconds': round(time.monotonic() - began, 3), 'real_sse_with_fake_provider': event_result,
            'private_reads_and_accounts_verified': True}


def main(unit):
    global PHASE
    group = check_isolation(unit)
    os.umask(0o077)
    for p in (STAGE, RUNTIME):
        demand(p.resolve() == p and p.is_dir() and p.stat().st_uid == os.getuid())
    harness = STAGE / 'rehearse_budget_runtime.py'
    demand(harness.resolve() == harness and sha(harness.read_bytes()) == HARNESS_SHA)
    sys.path.insert(0, str(STAGE))
    from rehearse_budget_runtime import read_archive, runtime_lock, WINDOWS_ID, FRONTEND_ID
    import httpx
    demand(runtime_lock(STAGE/'requirements-linux-cp312.lock', False) == 63)
    PHASE = 'native_candidate'
    with tempfile.TemporaryDirectory(prefix='travel-budget-load-', dir='/tmp') as tmp:
        work = Path(tmp)
        source, candidate = work/'source', work/'application'
        read_archive(STAGE/'application-source.tar.gz', source)
        sys.path.insert(0, str(source/'backend'))
        from tools.release_candidate import verify, prepare, runtime_report
        baseline = verify(source)
        demand(baseline['id'] == WINDOWS_ID and baseline['frontend_id'] == FRONTEND_ID)
        router = source/ROUTER
        router.write_bytes(patch_router(router.read_bytes()))
        native = prepare(source, source/'frontend', candidate)
        demand(native['files'] == (baseline['files'] | {ROUTER: PATCHED_SHA}) and native['frontend_id'] == FRONTEND_ID and
               runtime_report(candidate)['pinned_runtime_matches'])
        env_file = work/'synthetic.env'
        env_file.write_text('JWT_SECRET='+secrets.token_hex(32)+'\n')
        env = {'PATH': '/usr/bin:/bin', 'TRAVEL_ENV_FILE': str(env_file), 'TRAVEL_DATA_DIR': str(work/'data'),
               'TRAVEL_SHARED_LIMITS':'0', 'TRAVEL_SHARED_CACHE':'0', 'TRAVEL_PROVIDER_BUDGET':'0'}
        PHASE = 'boot'
        with (work/'service.log').open('wb') as log:
            child = subprocess.Popen([sys.executable, '-I', '-B', '-c', BOOT, str(candidate), str(work)], env=env,
                                     stdout=log, stderr=log, stdin=subprocess.DEVNULL, cwd=candidate/'backend')
            try:
                deadline = time.monotonic() + 24
                while not (work/'ready').exists():
                    demand(child.poll() is None and time.monotonic() < deadline)
                    time.sleep(.05)
                port = int((work/'ready').read_text())
                with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=5) as client:
                    while True:
                        demand(child.poll() is None and time.monotonic() < deadline)
                        try:
                            if client.get('/api/health').status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        time.sleep(.05)
                    PHASE = 'bounded_workload'
                    before = metrics(group)
                    result = workload(client)
                    after = metrics(group)
                    usage = {k: after[k]-before[k] for k in ('usage_usec','nr_throttled','oom','oom_kill')}
                    demand(after['oom'] == after['oom_kill'] == 0 and after['peak_bytes'] <= MEM_MAX)
                demand((work/'synthetic-chat-count').read_text() == '1\n' and not (work/'outbound-attempted').exists())
            finally:
                child.terminate()
                try:
                    child.wait(7)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(5)
        PHASE = 'cleanup'
        demand(child.poll() is not None and verify(candidate) == native)
        with socket.socket() as sock:
            demand(sock.connect_ex(('127.0.0.1', port)) != 0)
    demand(not work.exists())
    return {'status':'passed','unit':unit,'application_id':native['id'],'frontend_id':native['frontend_id'],
            'candidate_files':len(native['files']),'router_sha256':PATCHED_SHA,'python':platform.python_version(),
            'dependency_count':63,'network_only_loopback':True,'sandbox_cpu_cores':.5,'sandbox_task_limit':64,
            'sandbox_memory_limit_bytes':MEM_MAX,'sandbox_peak_bytes':after['peak_bytes'],'workload_cgroup_delta':usage,
            'synthetic_provider_calls':1,'real_provider_attempts':0,'temporary_data_removed':True,
            'direct_uvicorn_no_nginx':True,'production_changed':False,'production_ready':False, **result}


if __name__ == '__main__':
    try:
        def terminate(_signum, _frame):
            raise TimeoutError('Fixture expired')
        signal.signal(signal.SIGTERM, terminate)
        signal.signal(signal.SIGALRM, terminate)
        signal.alarm(65)
        print(json.dumps(main(sys.argv[1]), sort_keys=True), flush=True)
    except (Exception, KeyboardInterrupt) as error:
        print(json.dumps({'status':'failed','phase':PHASE,'error_type':type(error).__name__}), flush=True)
        raise SystemExit(1) from None
