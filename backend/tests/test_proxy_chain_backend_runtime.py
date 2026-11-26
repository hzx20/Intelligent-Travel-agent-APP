"""Validate the actual rehearsal bootstrap before sending it to Linux."""
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx
import pytest

from test_release_candidate_runtime import packaged_backend
import test_release_runtime as nginx_fixture
from test_release_runtime import port
from tools.qa_linux_proxy_chain import BACKEND_BOOTSTRAP, check_budget_review

ROOT = Path(__file__).resolve().parents[2]
BUDGET_CANDIDATE = ROOT / '.release-candidates/unified-application-2026-11-18-budget'


def test_rehearsal_backend_uses_synthetic_state_and_streams_separate_events(tmp_path):
    secret = tmp_path / 'synthetic.env'
    secret.write_text('JWT_SECRET=synthetic-bootstrap-test-key\nAMAP_API_KEY=\nZHIPU_API_KEY=\n')
    data = tmp_path / 'data'
    selected = port()
    env = dict(os.environ)
    env.update({'TRAVEL_ENV_FILE': str(secret), 'TRAVEL_DATA_DIR': str(data), 'PYTHONDONTWRITEBYTECODE': '1',
                'JWT_SECRET': 'synthetic-bootstrap-test-key', 'AMAP_API_KEY': '', 'ZHIPU_API_KEY': '',
                'AMAP_JS_KEY': '', 'AMAP_JS_SECURITY_CODE': '', 'TRAVEL_SHARED_LIMITS': '0',
                'TRAVEL_SHARED_CACHE': '0', 'TRAVEL_PROVIDER_BUDGET': '0'})
    for name in list(env):
        if name.lower() in ('pythonpath', 'pythonhome'):
            del env[name]
    with (tmp_path / 'backend.log').open('wb') as log:
        process = subprocess.Popen([sys.executable, '-B', '-c', BACKEND_BOOTSTRAP, str(ROOT), '127.0.0.1',
                                    str(selected), '127.0.0.2', str(secret), str(data)], cwd=ROOT / 'backend',
                                   env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{selected}', trust_env=False, timeout=5) as client:
            deadline = time.monotonic() + 25
            while True:
                assert process.poll() is None, (tmp_path / 'backend.log').read_text(errors='replace')
                try:
                    if client.get('/api/health').status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert time.monotonic() < deadline, 'Bootstrap did not become healthy'
                time.sleep(.1)
            assert client.get('/api/__fixture/client', headers={'X-Forwarded-For': '203.0.113.2',
                              'X-Forwarded-Proto': 'https'}).json() == {'client': '127.0.0.1', 'scheme': 'http'}
            with client.stream('GET', '/api/plan/stream') as response:
                events = response.iter_lines()
                assert next(events) == 'data: first'
                received = time.monotonic()
                assert next(events) == ''
                assert next(events) == 'data: second'
                assert time.monotonic() - received >= .6
            credentials = {'username': 'synthetic_bootstrap', 'password': 'synthetic-password'}
            assert client.post('/api/auth/register', json=credentials).status_code == 201
            token = client.post('/api/auth/login', json=credentials).json()['token']
            headers = {'Authorization': 'Bearer ' + token}
            body = {'title': 'Synthetic bootstrap trip', 'days': 1,
                    'request_id': '11111111-1111-4111-8111-111111111111'}
            first = client.post('/api/trips/create', json=body, headers=headers)
            replay = client.post('/api/trips/create', json=body, headers=headers)
            assert (first.status_code, replay.status_code) == (201, 200)
            assert first.json()['id'] == replay.json()['id']
            assert (data / 'app.db').is_file()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


@pytest.mark.skipif(not nginx_fixture.NGINX.is_file() or not BUDGET_CANDIDATE.is_dir(),
                    reason='Saved budget application and explicit nginx binary required')
def test_saved_budget_patch_conflict_and_ledger_via_nginx(tmp_path, monkeypatch):
    monkeypatch.setattr(nginx_fixture, 'ROOT', BUDGET_CANDIDATE)
    monkeypatch.setattr(nginx_fixture, 'DIST', BUDGET_CANDIDATE / 'frontend')
    with packaged_backend(BUDGET_CANDIDATE, tmp_path / 'data', tmp_path / 'proof.json') as (backend, _):
        with nginx_fixture.nginx_server(tmp_path / 'nginx', backend.base_url.port) as proxy:
            account = {'username': 'synthetic_budget', 'password': 'synthetic-nginx-password'}
            assert proxy.post('/travel/api/auth/register', json=account).status_code == 201
            login = proxy.post('/travel/api/auth/login', json=account)
            assert login.status_code == 200
            auth = {'Authorization': 'Bearer ' + login.json()['token']}
            created = proxy.post('/travel/api/trips/create', headers=auth,
                                 json={'title': 'Synthetic budget trip', 'days': 1,
                                       'request_id': '11111111-1111-4111-8111-111111111111'})
            assert created.status_code == 201
            check_budget_review(str(proxy.base_url).rstrip('/'), created.json()['id'], auth)
