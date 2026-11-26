"""Authorized Linux transient-unit rehearsal on synthetic namespace-mapped data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
from threading import Event, Thread
import time
import urllib.error
import urllib.request
import uuid

from tools.systemd_sandbox_fixture import crash_arguments, run_arguments, specification

PLAN_ID = 'effb123a9119956d419ba8e52aeaa307141254a10d775953330cc0721392fc42'
SOURCE_HASH = '300ba0f22714e4942ac5e7ddeb6d51269f0f6b4557e78c3364e04ff50e589f73'


def command(args, timeout=10):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def production_state():
    config = Path('/opt/continew/nginx.conf')
    backend = command(['systemctl', 'show', 'travel', '--property=MainPID', '--value'])
    nginx = command(['sudo', '-n', 'docker', 'exec', 'continew-web', 'cat', '/var/run/nginx.pid'])
    assert backend.returncode == nginx.returncode == 0
    return {'config_sha256': hashlib.sha256(config.read_bytes()).hexdigest(), 'backend_pid': backend.stdout.strip(),
            'nginx_pid': nginx.stdout.strip()}


def high_port(address):
    with socket.socket() as sock:
        sock.bind((address, 0)); port = sock.getsockname()[1]
    assert 20000 <= port <= 65535
    return port


def http(origin, path, body=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request(origin + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        response = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=2)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        content = response.read(1024 * 1024)
        return response.status, json.loads(content) if content else None


def inspect(root, ownership=False):
    args = ['sudo', '-n', '/usr/bin/python3', '-I', '-S', '-B', str(root / 'qa_systemd_inspect.py'),
            '--request', str(root / 'request.json')]
    if ownership:
        args.append('--ownership-only')
    result = command(args)
    if result.returncode:
        raise RuntimeError('Owned-unit inspection failed: ' + result.stderr[-900:])
    return json.loads(result.stdout)


def wait_healthy(root, origin, old_pid=None):
    deadline = time.monotonic() + 55
    last = None
    while time.monotonic() < deadline:
        last = inspect(root, ownership=True)
        if last.get('main_pid') and (old_pid is None or last['main_pid'] != old_pid):
            try:
                if http(origin, '/api/health')[0] == 200:
                    return inspect(root)
            except OSError:
                pass
        time.sleep(.2)
    raise TimeoutError('Transient service did not become healthy; inspect owned journal/preflight report')


def cleanup_unit(root, spec):
    snapshot = inspect(root, ownership=True)
    if snapshot['loaded']:
        assert snapshot.get('ownership_guard')
        stopped = command(['sudo', '-n', 'systemctl', 'stop', spec['unit_name']], timeout=20)
        assert stopped.returncode == 0
    # Restart=always must not restart after the explicit stop (RestartSec=3).
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        value = inspect(root, ownership=True)
        assert not value.get('main_pid')
        time.sleep(.3)
    deadline = time.monotonic() + 3
    while True:
        listed = command(['systemctl', 'list-units', '--all', '--plain', '--no-legend', '--full', spec['unit_name']])
        assert listed.returncode == 0
        if not listed.stdout.strip():
            break
        assert time.monotonic() < deadline, 'Transient unit has not been collected'
        time.sleep(.2)
    group = Path('/sys/fs/cgroup/system.slice') / spec['unit_name']
    assert not group.exists() or not (group / 'cgroup.procs').read_text().strip()
    assert not Path('/run/systemd/transient', spec['unit_name']).exists()
    leftovers = []
    for parent in (Path('/tmp'), Path('/var/tmp')):
        leftovers += [str(p) for p in parent.glob('systemd-private-*-' + spec['unit_name'] + '-*')]
    return {'unit_unloaded': True, 'cgroup_has_no_processes': True, 'transient_fragment_absent': True,
            'private_tmp_directories': leftovers}


def run(root):
    root = Path(root).absolute(); base = root.parent
    assert root.resolve() == root and re.fullmatch(r'/tmp/travel-release-rehearsal-\d{8}-[0-9a-f]{8}', str(base))
    account = pwd.getpwnam('ubuntu')
    assert root.stat().st_uid == os.getuid() == account.pw_uid and not root.stat().st_mode & 0o077
    assert next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')) >= 768 * 1024
    expected = json.loads((root / 'expected.json').read_text())
    for name, checksum in expected['files'].items():
        assert (root / name).resolve().is_relative_to(root) and hashlib.sha256((root / name).read_bytes()).hexdigest() == checksum
    plan_root = base / 'unified-plan-20261117-review1/plan-linux'
    runtime = base / 'runtime-env-20261117-review1/venv'
    sys.path.insert(0, str(plan_root))
    from tools.release_plan import verify
    plan = verify(plan_root, expected_id=PLAN_ID)
    app_source = plan_root / 'application'
    before = production_state(); assert before['config_sha256'] == SOURCE_HASH
    formal_code = Path('/opt/travel/releases') / plan['application_id']
    formal_runtime = Path('/opt/travel/runtime-envs') / plan['application_id']
    assert not formal_code.exists() and not formal_runtime.exists()
    os.umask(0o077)
    work = root / 'work'; work.mkdir(mode=0o700)
    data = work / 'data'; data.mkdir(mode=0o700)
    probe = work / 'probe'; probe.mkdir(mode=0o700)
    env_file = work / 'synthetic.env'
    env_file.write_text('JWT_SECRET=' + secrets.token_hex(32) + '\nAMAP_API_KEY=\nZHIPU_API_KEY=\nAMAP_JS_KEY=\nAMAP_JS_SECURITY_CODE=\n')
    sentinel = work / 'host-only-sentinel'; sentinel.write_text('SYNTHETIC_HOST_TMP_SENTINEL')
    shutil.copyfile(root / 'qa_systemd_preflight.py', probe / 'preflight.py')
    unit_text = (plan_root / 'ops/travel.service').read_text()
    nonce = uuid.uuid4().hex
    service_port = high_port('127.0.0.1')
    sink = socket.socket(); sink.bind((plan['backend_bind_ip'], 0)); sink.listen(8); sink.settimeout(.1)
    sink_port = sink.getsockname()[1]
    assert 20000 <= sink_port <= 65535 and sink_port != service_port
    stop_sink = Event(); accepted = []
    def accept():
        while not stop_sink.is_set():
            try:
                connection, _ = sink.accept(); accepted.append(time.monotonic()); connection.close()
            except socket.timeout:
                pass
            except OSError:
                break
    sink_thread = Thread(target=accept, daemon=True); sink_thread.start()
    with socket.create_connection((plan['backend_bind_ip'], sink_port), timeout=2):
        pass
    deadline = time.monotonic() + 2
    while not accepted and time.monotonic() < deadline:
        time.sleep(.05)
    assert len(accepted) == 1
    spec = specification(plan, unit_text, str(base), str(root), str(app_source), str(runtime), nonce, service_port, sink_port)
    def identity(path):
        info = path.stat(); return [info.st_dev, info.st_ino]
    probe_config = {'uid': account.pw_uid, 'gid': account.pw_gid, 'formal_code': spec['formal_code'],
                    'formal_runtime': spec['formal_runtime'], 'synthetic_env_identity': identity(env_file),
                    'synthetic_env_sha256': hashlib.sha256(env_file.read_bytes()).hexdigest(),
                    'synthetic_data_identity': identity(data), 'application_main_identity': identity(app_source / 'backend/app/main.py'),
                    'venv_config_identity': identity(runtime / 'pyvenv.cfg'), 'host_sentinel': str(sentinel),
                    'host_network_namespace': os.readlink('/proc/self/ns/net'), 'sink_ip': spec['sink_ip'],
                    'sink_port': sink_port, 'expected_environment': spec['environment']}
    (probe / 'config.json').write_text(json.dumps(probe_config, indent=2))
    request = {'stage': str(root), 'work': str(work), 'application_source': str(app_source), 'venv_source': str(runtime),
               'host_sentinel': str(sentinel), 'spec': spec}
    (root / 'request.json').write_text(json.dumps(request, indent=2))
    (root / 'start-arguments.json').write_text(json.dumps(run_arguments(spec), indent=2))
    report = {'success': False, 'plan_id': PLAN_ID, 'application_id': plan['application_id'], 'unit_name': spec['unit_name'],
              'production_ready': False, 'production_started_or_restarted': False, 'production_database_accessed': False,
              'namespace_only_formal_paths': True, 'qa_differences': spec['qa_differences']}
    started = time.monotonic()
    try:
        existing = inspect(root, ownership=True); assert not existing['loaded']
        launched = command(run_arguments(spec), timeout=25)
        (root / 'systemd-run.log').write_text(launched.stdout + launched.stderr)
        assert launched.returncode == 0, 'Transient creation failed; inspect private log'
        origin = 'http://127.0.0.1:' + str(service_port)
        first = wait_healthy(root, origin)
        assert not formal_code.exists() and not formal_runtime.exists()
        report['first_start'] = first
        credentials = {'username': 'synthetic_systemd_' + nonce[:8], 'password': 'synthetic-systemd-password'}
        assert http(origin, '/api/auth/register', credentials)[0] == 201
        status, login = http(origin, '/api/auth/login', credentials); assert status == 200
        token = login['token']
        creation = {'title': 'Synthetic systemd namespace trip', 'days': 1, 'request_id': str(uuid.uuid4())}
        status, trip = http(origin, '/api/trips/create', creation, token); assert status == 201
        trip_id = trip['id']
        guard = inspect(root, ownership=True)
        assert guard['ownership_guard'] and guard['main_pid'] == first['main_pid']
        killed = command(crash_arguments(spec))
        (root / 'crash-command.log').write_text(killed.stdout + killed.stderr)
        assert killed.returncode == 0
        second = wait_healthy(root, origin, old_pid=first['main_pid'])
        assert not formal_code.exists() and not formal_runtime.exists()
        assert second['main_pid'] != first['main_pid'] and second['n_restarts'] >= first['n_restarts'] + 1
        assert http(origin, '/api/me/profile', token=token)[0] == 200
        status, replay = http(origin, '/api/trips/create', creation, token)
        assert status == 200 and replay['id'] == trip_id
        assert http(origin, '/api/trips/' + str(trip_id), token=token)[0] == 200
        probes = [json.loads(p.read_text()) for p in data.glob('preflight-*.json')]
        assert len(probes) == 2 and all(p['success'] for p in probes)
        assert len(accepted) == 1
        report.update({'second_start': second, 'namespace_preflight_reports': probes, 'owned_sink_parent_control_succeeded': True,
                       'no_service_connection_accepted_by_non_loopback_sink': True,
                       'automatic_restart_after_crash': True, 'jwt_survives_restart': True,
                       'formal_paths_absent_in_host_while_service_running': True,
                       'reliable_create_replays_after_restart': True, 'success': True})
    finally:
        signal.signal(signal.SIGHUP, signal.SIG_IGN); signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGALRM, signal.SIG_IGN); signal.alarm(0)
        try:
            journal = command(['sudo', '-n', 'journalctl', '--unit=' + spec['unit_name'], '--no-pager', '-n', '50', '-o', 'cat'])
            (root / 'owned-unit-journal.log').write_text(journal.stdout[-20000:] + journal.stderr[-1000:])
        except Exception as error:
            report['journal_capture_error'] = type(error).__name__
        try:
            report['cleanup'] = cleanup_unit(root, spec)
            clean = not report['cleanup']['private_tmp_directories']
        except Exception as error:
            clean = False; report['cleanup_error'] = type(error).__name__ + ': ' + str(error)[-800:]
        stop_sink.set(); sink.close(); sink_thread.join(timeout=2)
        report['owned_sink_stopped'] = not sink_thread.is_alive()
        if clean:
            assert work.resolve().parent == root
            shutil.rmtree(work)
        report['synthetic_work_removed'] = not work.exists()
        report['formal_host_paths_still_absent'] = not formal_code.exists() and not formal_runtime.exists()
        report['plan_unchanged'] = verify(plan_root, expected_id=PLAN_ID)['id'] == PLAN_ID
        report['production_state_unchanged'] = production_state() == before
        report['elapsed_seconds'] = round(time.monotonic() - started, 2)
        (root / 'result.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report), flush=True)
    assert report['success'] and report['synthetic_work_removed'] and report['owned_sink_stopped']
    assert report['formal_host_paths_still_absent'] and report['plan_unchanged'] and report['production_state_unchanged']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review-dir', required=True)
    args = parser.parse_args()
    def interrupted(signum, frame):
        raise InterruptedError('Transient service rehearsal interrupted')
    signal.signal(signal.SIGHUP, interrupted); signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGALRM, interrupted); signal.alarm(180)
    run(args.review_dir)
