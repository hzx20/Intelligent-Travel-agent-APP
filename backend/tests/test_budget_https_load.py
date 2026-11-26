"""Test the frozen travel upgrade through TLS on a local, bounded fixture only."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import time
from uuid import uuid4

import httpx
import pytest

from test_release_candidate_runtime import packaged_backend
import test_release_runtime as fixture
from tools.release_candidate import prepare, verify


APP = Path(__file__).resolve().parents[2] / '.release-candidates/unified-application-2026-11-18-budget'
CANDIDATE = '9349719f177f76155a3b13e02fe9e7609f7b673b3401a749050f2da548d56b04'
TRIPS = 'backend/app/routers/trips.py'


def certificate_files(tmp):
    openssl = os.environ.get('TRAVEL_TEST_OPENSSL') or shutil.which('openssl')
    if not openssl:
        pytest.skip('OpenSSL required for a temporary trusted TLS certificate')
    certificate, key = tmp / 'loopback.pem', tmp / 'loopback.key'
    result = subprocess.run([openssl, 'req', '-x509', '-sha256', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                             '-subj', '/CN=127.0.0.1', '-addext', 'subjectAltName=IP:127.0.0.1',
                             '-addext', 'extendedKeyUsage=serverAuth', '-keyout', str(key), '-out', str(certificate)],
                            capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    return certificate, key


def exercise(proxy, certificate, manifest):
    assert proxy.base_url.scheme == 'https' and proxy.base_url.host == '127.0.0.1'
    health = proxy.get('/travel/api/health')
    stream = health.extensions['network_stream']
    tls = stream.get_extra_info('ssl_object').version()
    assert health.status_code == 200 and health.json() == {'status': 'ok'} and tls in ('TLSv1.2', 'TLSv1.3')
    for url, trust in ((str(proxy.base_url), True), (f'https://localhost:{proxy.base_url.port}',
                                                    ssl.create_default_context(cafile=str(certificate)))):
        with httpx.Client(verify=trust, trust_env=False, timeout=3) as client:
            with pytest.raises(httpx.ConnectError, match='CERTIFICATE_VERIFY_FAILED'):
                client.get(url)

    slash = proxy.get('/travel', follow_redirects=False)
    assert slash.status_code == 301 and slash.headers['location'] == f'{str(proxy.base_url).rstrip("/")}/travel/'
    home = proxy.get('/travel/')
    assert home.status_code == 200 and hashlib.sha256(home.content).hexdigest() == manifest['files']['frontend/index.html']
    assert proxy.get('/travel/.vite/manifest.json').status_code == proxy.get('/travel/assets/missing.js').status_code == 404
    assets = {f.removeprefix('frontend/'): h for f, h in manifest['files'].items() if f.startswith('frontend/assets/')}
    for asset, digest in assets.items():
        response = proxy.get('/travel/' + asset)
        assert response.status_code == 200 and 'immutable' in response.headers['cache-control']
        assert hashlib.sha256(response.content).hexdigest() == digest

    exact_password = 'sample-password-not-a-real-user'
    accounts = [{'username': f'tls_synthetic_{i}', 'password': exact_password} for i in range(2)]
    paths = []
    for account in accounts:
        assert proxy.post('/travel/api/auth/register', json=account).status_code == 201
        login = proxy.post('/travel/api/auth/login', json=account)
        assert login.status_code == 200
        headers = {'Authorization': 'Bearer ' + login.json()['token']}
        created = proxy.post('/travel/api/trips/create', headers=headers,
                             json={'title': 'TLS simulation', 'days': 1, 'request_id': str(uuid4())})
        assert created.status_code == 201
        paths.append((f'/travel/api/trips/{created.json()["id"]}', headers, created.json()['version']))
    left, right = paths
    assert proxy.get(left[0], headers=right[1]).status_code == 404

    popular_asset = '/travel/' + next(iter(assets))
    def operation(i):
        start = time.monotonic()
        case = i % 4
        if case == 0:
            target = proxy.get('/travel/trips/1?panel=budget')
            assert target.status_code == 200 and target.content == home.content and 'no-store' in target.headers['cache-control']
        elif case == 1:
            target = proxy.get(popular_asset)
            assert target.status_code == 200 and 'immutable' in target.headers['cache-control']
        elif case == 2:
            record = paths[(i // 4) % 2]
            target = proxy.get(record[0], headers=record[1])
            assert target.status_code == 200 and target.headers.get('cache-control') == 'private, no-store'
        else:
            assert proxy.get('/travel/api/health').status_code == 200
        return (time.monotonic() - start) * 1000

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=8) as pool:
        times = sorted(pool.map(operation, range(160)))
        path, headers, version = left
        quote = {'status': 'quoted', 'amount_minor': 98000, 'source': 'Synthetic accommodation', 'checked_on': '2026-11-19'}
        patch = {'version': version, 'checklist': {'limit_minor': 300000, 'entries': {'lodging': quote}}}
        results = tuple(pool.map(lambda _: proxy.patch(path + '/budget-review', json=patch, headers=headers).status_code, range(2)))
    assert sorted(results) == [200, 409]
    saved = proxy.get(path, headers=headers).json()
    assert saved['version'] == version + 1 and saved['budget_review']['checklist']['entries']['lodging'] == quote
    assert proxy.get(path + '/expenses', headers=headers).json()['items'] == []
    return {'tls_protocol': tls, 'certificate_and_hostname_verified': True, 'invalid_trust_and_hostname_rejected': True,
            'static_asset_hashes_verified': len(assets), 'read_requests': len(times), 'client_concurrency': 8,
            'read_failures': 0, 'read_p95_ms': round(times[math.ceil(.95 * len(times)) - 1], 2),
            'read_max_ms': round(times[-1], 2), 'read_and_write_seconds': round(time.monotonic() - started, 3),
            'simultaneous_patch_statuses': sorted(results), 'distinct_synthetic_accounts_isolated': True}


def test_budget_https_and_bounded_mixed_load(tmp_path, monkeypatch):
    if not APP.is_dir() or not fixture.NGINX.is_file():
        pytest.skip('Frozen budget app and nginx binary required')
    baseline = verify(APP)
    assert baseline['id'] == CANDIDATE
    fixed = (fixture.ROOT / TRIPS).read_bytes()
    # Prepare a new ID and leave the already rehearsed Linux/Windows artifacts intact.
    with tempfile.TemporaryDirectory(prefix='tls-runtime-', dir=tmp_path) as directory:
        work = Path(directory)
        source = work / 'source'
        shutil.copytree(APP, source)
        (source / TRIPS).write_bytes(fixed)
        candidate = work / 'candidate'
        metadata = prepare(source, source / 'frontend', candidate)
        assert {p for p, v in metadata['files'].items() if baseline['files'][p] != v} == {TRIPS}
        monkeypatch.setattr(fixture, 'ROOT', candidate)
        monkeypatch.setattr(fixture, 'DIST', candidate / 'frontend')
        certificate, key = certificate_files(work)
        with packaged_backend(candidate, work / 'data', work / 'app-proof.json', shared=False, proxy_headers=True) as (backend, _):
            with fixture.nginx_server(work / 'nginx', backend.base_url.port, certificate=certificate, key=key) as proxy:
                result = exercise(proxy, certificate, metadata)
        assert verify(candidate) == metadata
    assert not work.exists() and verify(APP) == baseline
    print('BUDGET_HTTPS_RESULT ' + json.dumps({'application_id': metadata['id'], 'baseline_id': CANDIDATE,
          'router_sha256': metadata['files'][TRIPS], 'frontend_id': metadata['frontend_id'], 'scratch_removed': True, **result}))
