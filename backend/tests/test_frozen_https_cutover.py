"""Run the final frozen app behind the reviewed HTTP and HTTPS travel routes."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import ssl
import subprocess
import time

import httpx
import pytest

from test_release_candidate_runtime import packaged_backend
from test_release_runtime import NGINX, port
from test_travel_https import cert_pair
from tools import frontend_release, nginx_merge, release_candidate, release_plan, travel_https


REVIEW = Path(__file__).resolve().parents[2] / '.release-candidates/supplier-city-2026-11-20'
BUNDLE = REVIEW / 'private-https-companion'
APP = REVIEW / 'private-plan/application'
PUBLIC = '134.175.137.18'
APPLICATION_ID = '28ba4881d397e73dd76bfed0ade8f8533d11510fb2a2f347192fd07bb76cc281'
BUNDLE_ID = 'f85a32dea9051cb7379b34ddba704f26ed4f990b0e728e1d61b4eef22ded70bd'
pytestmark = pytest.mark.skipif(not NGINX.is_file() or not (BUNDLE / 'report.json').is_file() or not APP.is_dir(),
                                reason='Requires the frozen application, paired HTTPS review files and portable nginx')


def reviewed_configs():
    manifest = json.loads((BUNDLE / 'report.json').read_bytes())
    assert manifest.pop('id') == release_plan.identifier(manifest) == BUNDLE_ID
    assert manifest['application_id'] == APPLICATION_ID
    names = ('nginx-http-after-tls.conf', 'nginx-edge-maintenance.conf', 'nginx-edge.conf')
    configs = {}
    for name in names:
        raw = (BUNDLE / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == manifest['files'][name]
        configs[name] = raw.decode('utf-8')
    return configs


def loopback_config(raw_http, raw_edge, plain_port, tls_port, backend_port, keypair):
    _, _, server, _ = travel_https.layout(raw_http, expect_normal=False)
    travel = {('=', '/travel/api'), ('^~', '/travel/api/'), ('=', '/travel'), ('^~', '/travel/'),
              ('^~', '/.well-known/acme-challenge/')}
    chosen = [node for node in server.children if node.head[0] == 'location' and node.head[1:] in travel]
    assert len(chosen) == len(travel) and {node.head[1:] for node in chosen} == travel
    cleartext = '\n'.join(raw_http[node.start:node.end] for node in chosen)
    side = (f'server {{ listen 127.0.0.1:{plain_port}; location = / {{ return 200 "isolated other site"; }} '
            + cleartext + '\n}')
    cert, private = keypair
    edge = (raw_edge.replace('http {', 'http {\n' + side, 1)
            .replace('listen 443 ssl default_server;', f'listen 127.0.0.1:{tls_port} ssl default_server;')
            .replace('pid /tmp/travel-nginx.pid;', 'pid logs/nginx.pid;')
            .replace('error_log stderr warn;', 'error_log logs/error.log warn;')
            .replace('/tmp/travel-', 'temp/travel-')
            .replace('/usr/share/nginx/html', 'html')
            .replace('/etc/nginx/mime.types', f'"{(NGINX.parent / "conf/mime.types").as_posix()}"')
            .replace('/etc/nginx/travel-tls/current/fullchain.pem', f'"{cert.as_posix()}"')
            .replace('/etc/nginx/travel-tls/current/privkey.pem', f'"{private.as_posix()}"')
            .replace('http://172.31.240.1:8000/', f'http://127.0.0.1:{backend_port}/'))
    nodes = list(nginx_merge.all_nodes(nginx_merge.parse(edge)))
    assert [node.head[1] for node in nodes if node.head[0] == 'listen'] == [
        f'127.0.0.1:{plain_port}', f'127.0.0.1:{tls_port}',
    ]
    assert all(node.head[1] in (f'http://127.0.0.1:{backend_port}/api/',
                                f'http://127.0.0.1:{backend_port}/api/plan/stream')
               for node in nodes if node.head[0] == 'proxy_pass')
    return 'daemon off; master_process off;\n' + edge


@contextmanager
def nginx(root, raw_http, raw_edge, backend_port, keypair):
    for name in ('logs', 'temp', 'html/.well-known/acme-challenge'):
        (root / name).mkdir(parents=True)
    (root / 'html/.well-known/acme-challenge/fixture-token').write_text('fixture-token-bytes', encoding='ascii')
    frontend_release.publish(APP / 'frontend', root / 'html/travel')
    plain_port, tls_port = port(), port()
    while plain_port == tls_port:
        tls_port = port()
    (root / 'nginx.conf').write_text(loopback_config(raw_http, raw_edge, plain_port, tls_port, backend_port, keypair),
                                   encoding='utf-8')
    command = [str(NGINX), '-p', root.as_posix() + '/', '-c', 'nginx.conf']
    checked = subprocess.run([*command, '-t'], capture_output=True, timeout=10)
    assert checked.returncode == 0, checked.stderr.decode(errors='replace')
    child = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        trust = ssl.create_default_context(cafile=str(keypair[0]))
        with (httpx.Client(base_url=f'http://127.0.0.1:{plain_port}', trust_env=False, follow_redirects=False,
                           timeout=4) as plain,
              httpx.Client(base_url=f'https://127.0.0.1:{tls_port}', trust_env=False, verify=trust, timeout=4,
                           headers={'Host': PUBLIC}) as secure):
            deadline = time.monotonic() + 8
            while True:
                assert child.poll() is None and time.monotonic() < deadline
                try:
                    if plain.get('/').status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(.05)
            yield plain, secure
    finally:
        child.terminate()
        try:
            child.wait(6)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(4)


def test_reviewed_routes_maintenance_normal_and_maintenance_again(tmp_path):
    candidate = release_candidate.verify(APP)
    configs = reviewed_configs()
    assert candidate['id'] == APPLICATION_ID
    cert = cert_pair(tmp_path, PUBLIC, 'ip')
    secure_options = {'extensions': {'sni_hostname': PUBLIC}}
    user = {'username': 'cutover_fixture', 'password': 'synthetic-local-password'}
    with packaged_backend(APP, tmp_path / 'data', tmp_path / 'proof.json', shared=False, proxy_headers=True) as (backend, _):
        assert backend.post('/api/auth/register', json=user).status_code == 201
        last_trip = None
        for step, mode in enumerate(('maintenance', 'normal', 'maintenance')):
            filename = 'nginx-edge.conf' if mode == 'normal' else 'nginx-edge-maintenance.conf'
            with nginx(tmp_path / f'p{step}', configs['nginx-http-after-tls.conf'], configs[filename],
                       backend.base_url.port, cert) as (plain, secure):
                page = '/travel/trips/1?panel=budget&tag=%E6%97%85'
                for method in ('GET', 'HEAD'):
                    redirect = plain.request(method, page, headers={'Host': 'placeholder.invalid'})
                    assert (redirect.status_code, redirect.headers['location']) == (308, f'https://{PUBLIC}{page}')
                assert plain.post('/travel/trips/1', json={}).status_code == 403
                assert plain.post('/travel/api/auth/login', json=user).status_code == 403
                assert plain.get('/travel/api').status_code == plain.get('/travel/api/health').status_code == 403
                assert plain.get('/').text == 'isolated other site'
                challenge = '/.well-known/acme-challenge/'
                assert plain.get(challenge + 'fixture-token').text == 'fixture-token-bytes'
                assert plain.get(challenge + 'missing').status_code == 404
                assert plain.post(challenge + 'fixture-token').status_code == 405
                assert secure.get('/', **secure_options).status_code == 404
                if mode == 'maintenance':
                    for path in ('/travel/', '/travel/api/health', page):
                        reply = secure.get(path, **secure_options)
                        assert reply.status_code == 503 and reply.headers['cache-control'] == 'no-store'
                        assert reply.headers['retry-after'] == '60'
                    assert secure.post('/travel/api/trips/create', json={}, **secure_options).status_code == 503
                    continue

                assert secure.get('/travel/api/health', **secure_options).json() == {'status':'ok'}
                assert secure.get('/travel/api/trips', **secure_options).status_code == 401
                reply = secure.get(page, **secure_options)
                assert reply.status_code == 200 and reply.headers['cache-control'] == 'no-store'
                assert reply.content == (APP / 'frontend/index.html').read_bytes()
                for name, checksum in candidate['files'].items():
                    if name.startswith('frontend/assets/'):
                        asset = secure.get('/travel/' + name.removeprefix('frontend/'), **secure_options)
                        assert asset.status_code == 200 and 'immutable' in asset.headers['cache-control']
                        assert hashlib.sha256(asset.content).hexdigest() == checksum
                state_file = '.travel-deploy/' + candidate['frontend_id'] + '.json'
                for hidden in ('.vite/manifest.json', state_file, 'assets/missing.js'):
                    assert secure.get('/travel/' + hidden, **secure_options).status_code == 404
                login = secure.post('/travel/api/auth/login', json=user, **secure_options)
                assert login.status_code == 200
                auth = {'Authorization': 'Bearer ' + login.json()['token'], 'Host': PUBLIC}
                assert secure.get('/travel/api/auth/me', headers=auth, **secure_options).json()['username'] == user['username']
                created = secure.post('/travel/api/trips/create', headers=auth, **secure_options, json={
                    'title': 'Cutover synthetic trip', 'days': 1, 'request_id': '28213716-c00a-11ee-9ec7-0242ac120002',
                })
                assert created.status_code == 201
                last_trip = created.json()['id']
        assert backend.get(f'/api/trips/{last_trip}', headers=auth).json()['title'] == 'Cutover synthetic trip'
    assert release_candidate.verify(APP) == candidate and reviewed_configs() == configs
