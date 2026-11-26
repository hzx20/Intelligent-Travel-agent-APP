"""Exercise the HTTPS design on one local nginx with separate cleartext and TLS listeners."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import time

import httpx
import pytest

from test_release_candidate_runtime import packaged_backend
import test_release_runtime as fixture
from tools import travel_https as tls
from tools.nginx_merge import one, signature, parse
from tools.release_candidate import verify

ROOT = Path(__file__).resolve().parents[2]
REVIEW = ROOT / '.release-candidates/budget-https-load-2026-11-19'
PORT80 = 'listen 80;'


def source():
    template = (REVIEW / 'application/deploy/nginx-travel.conf').read_text(encoding='utf-8')
    controls, routes = template.split('# ---- 放在 server 块内', 1)
    http = controls.split('# ---- 放在 http 块内', 1)[1].split('\n', 1)[1]
    locations = routes.split('\n', 1)[1]
    return ('events {worker_connections 128;}\nhttp {\n' + http +
            'server {listen 80; location = / { return 200 "OTHER SITE"; }\n' + locations + '\n}\n}\n')


def test_exact_plan_remains_intact_and_conflicting_inputs_are_refused():
    plan = REVIEW / 'private-plan'
    manifest = json.loads((plan / 'plan.json').read_bytes())
    shared = (plan / 'ops/nginx-normal.conf').read_bytes().decode('utf-8')
    assert tls.sha(shared) == manifest['files']['ops/nginx-normal.conf']
    assert 'server_name travel.fixture.test;' in tls.render(shared, 'travel.fixture.test', 'dns')
    original = (plan / 'ops/nginx-before.conf').read_bytes().decode('utf-8')
    bootstrap = tls.challenge_only(original)
    assert signature(parse(bootstrap.replace('\n' + tls.ACME + '\n', ''))) == signature(parse(original))
    mounted = tls.challenge_only(original, existing_mount=True)
    assert mounted.replace('\n' + tls.public_challenge(True) + '\n', '') == original
    with pytest.raises(ValueError):
        tls.render(shared, 'travel.fixture.test;evil', 'dns')
    with pytest.raises(ValueError):
        tls.render(shared, '127.0.0.1', 'ip')
    with pytest.raises(ValueError):
        tls.render(shared.replace('80;', '443 ssl;', 1), 'travel.fixture.test', 'dns')
    with pytest.raises(ValueError):
        tls.challenge_only(bootstrap)


def test_standalone_edge_does_not_copy_other_site_or_change_its_http_rules():
    plan = REVIEW / 'private-plan/ops'
    current = (plan/'nginx-before.conf').read_bytes().decode('utf-8')
    normal = (plan/'nginx-normal.conf').read_bytes().decode('utf-8')
    companion = tls.cleartext_companion(current, '134.175.137.18', 'ip', acme_webroot=True)
    for mode, name in [('dns','travel.fixture.test'), ('ip','134.175.137.18')]:
        edge = tls.standalone_edge(normal, name, mode)
        no_api = tls.standalone_edge(normal, name, mode, maintenance=True)
        assert all(n.head[1] == '443' for n in tls.all_nodes(parse(edge)) if n.head[0] == 'listen')
        assert 'http://172.18.0.1:8000/api/' in edge and 'proxy_pass' not in no_api and 'return 503' in no_api
        assert 'continew' not in edge and 'auth/login' not in no_api
    old, _, _, old_travel = tls.layout(current, expect_normal=False)
    new, _, _, new_travel = tls.layout(companion, expect_normal=False)
    excluded = {n.start for n in new_travel} | {n.start for n in tls.all_nodes(new) if n.head[-1] == '/.well-known/acme-challenge/'}
    assert signature(old, {n.start for n in old_travel}) == signature(new, excluded)


def cert_pair(root, host, mode):
    openssl = os.environ.get('TRAVEL_TEST_OPENSSL') or shutil.which('openssl')
    if not openssl:
        pytest.skip('OpenSSL required')
    pem, key = root / 'fixture.pem', root / 'fixture.key'
    result = subprocess.run([openssl, 'req', '-x509', '-sha256', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                             '-subj', '/CN='+host, '-addext', 'subjectAltName=' + ('IP:' if mode=='ip' else 'DNS:') + host,
                             '-addext', 'extendedKeyUsage=serverAuth', '-keyout', str(key), '-out', str(pem)],
                            timeout=12, capture_output=True)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    return pem, key


@contextmanager
def nginx(root, host, mode, backend_port, *, maintenance, standalone=False, legacy_challenge=False):
    root.mkdir()
    (root/'logs').mkdir()
    (root/'temp').mkdir()
    (root/'public').mkdir()
    (root/'public/index.html').write_text('OTHER SITE FALLBACK', encoding='ascii')
    shutil.copytree(REVIEW/'application/frontend', root/'public/travel')
    webroot = root/'public/.well-known/acme-challenge'
    webroot.mkdir(parents=True)
    (webroot/'fixture-token').write_text('challenge-payload', encoding='ascii')
    cert, key = cert_pair(root, host, mode)
    http, tls_port = fixture.port(), fixture.port()
    while tls_port == http:
        tls_port = fixture.port()
    if standalone:
        conf = tls.standalone_edge(source(), host, mode, maintenance=maintenance)
        # Keep the probe HTTP socket unrelated to travel. Only the standalone
        # generator supplies the TLS routes and controls in this fixture.
        other = 'server {listen 80; location / {return 200 "OTHER SITE";}}'
        if legacy_challenge:
            original = (REVIEW/'private-plan/ops/nginx-before.conf').read_bytes().decode('utf-8')
            shared = one(one(parse(original),'http').children,'server')
            fallback, = [node for node in shared.children if node.head == ('location','/')]
            other = 'server {listen 80; root public; index index.html; ' + original[fallback.start:fallback.end] + '}'
        conf = (conf.replace('http {', 'http {\n'+other, 1).replace('pid /tmp/travel-nginx.pid;', '')
                .replace('error_log stderr warn;', '').replace('/tmp/travel-', 'temp/travel-')
                .replace('/etc/nginx/mime.types', '"'+(fixture.NGINX.parent/'conf/mime.types').as_posix()+'"'))
    else:
        conf = tls.render(source(), host, mode, maintenance=maintenance, acme_webroot=mode=='ip', existing_mount=mode=='ip')
    conf = ('daemon off; master_process off; pid logs/nginx.pid; error_log logs/error.log warn;\n' + conf
            .replace(PORT80, f'listen 127.0.0.1:{http};')
            .replace('listen 443 ssl', f'listen 127.0.0.1:{tls_port} ssl')
            .replace(tls.CERT, f'"{cert.as_posix()}"').replace(tls.KEY, f'"{key.as_posix()}"')
            .replace('/usr/share/nginx/html', 'public').replace('/var/lib/travel-acme', 'acme')
            .replace('http://172.18.0.1:8000/', f'http://127.0.0.1:{backend_port}/'))
    listen = [n.head[1] for n in tls.all_nodes(parse(conf)) if n.head[0] == 'listen']
    assert all(v in (f'127.0.0.1:{http}', f'127.0.0.1:{tls_port}') for v in listen)
    (root/'nginx.conf').write_text(conf, encoding='utf-8')
    args = [str(fixture.NGINX), '-p', root.as_posix()+'/', '-c', 'nginx.conf']
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    check = subprocess.run([*args, '-t'], capture_output=True, timeout=10, creationflags=flags)
    assert check.returncode == 0, check.stderr.decode(errors='replace')
    try:
        child = subprocess.Popen(args, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, creationflags=flags)
        with httpx.Client(base_url=f'http://127.0.0.1:{http}',trust_env=False,timeout=3) as plain:
            end = time.monotonic()+8
            while True:
                assert child.poll() is None and time.monotonic()<end
                try:
                    if plain.get('/').status_code==200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(.05)
            context = ssl.create_default_context(cafile=str(cert))
            with httpx.Client(base_url=f'https://127.0.0.1:{tls_port}',verify=context,trust_env=False,timeout=4,
                              limits=httpx.Limits(max_keepalive_connections=0)) as secure:
                yield plain, secure
    finally:
        if 'child' in locals():
            child.terminate()
            try:
                child.wait(6)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(4)


@pytest.fixture
def scratch(tmp_path):
    with tempfile.TemporaryDirectory(dir=tmp_path) as work:
        yield Path(work)
    assert not Path(work).exists()


@pytest.mark.parametrize('mode,host', [('dns','travel.fixture.test'), ('ip','134.175.137.18')])
def test_shared_http_is_preserved_while_travel_tls_routes_are_verified(scratch, mode, host):
    if not fixture.NGINX.is_file():
        pytest.skip('Portable nginx is required')
    candidate = REVIEW/'application'
    before = verify(candidate)
    with packaged_backend(candidate,scratch/'data',scratch/'proof.json',shared=False,proxy_headers=True) as (backend, _):
        with nginx(scratch/'normal', host, mode, backend.base_url.port, maintenance=False) as (plain, secure):
            opts = {'headers': {'Host':host}, 'extensions': {'sni_hostname':host}}
            assert plain.get('/').text == 'OTHER SITE'
            deep = '/travel/trips/1?panel=budget&tag=%E6%97%85'
            redir = plain.get(deep, headers={'Host':'not-the-travel-host.invalid'})
            assert redir.status_code == 308 and redir.headers['location'] == 'https://' + host + deep
            assert plain.post('/travel/api/auth/login', json={'username':'synthetic'}).status_code == 403
            assert plain.get('/travel/api/health').status_code == plain.post('/travel/trips/1').status_code == 403
            if mode=='ip':
                challenge = plain.get('/.well-known/acme-challenge/fixture-token')
                assert challenge.status_code == 200 and challenge.text == 'challenge-payload'
                assert plain.get('/.well-known/acme-challenge/missing').status_code == 404
                assert plain.post('/.well-known/acme-challenge/fixture-token').status_code == 405
            assert secure.get('/', **opts).status_code == 404
            assert secure.get('/travel?keep=yes', **opts).headers['location'] == '/travel/?keep=yes'
            assert secure.get(deep, **opts).content == (candidate/'frontend/index.html').read_bytes()
            assert secure.get('/travel/.vite/manifest.json', **opts).status_code == 404
            assert secure.get('/travel/api/health', **opts).json() == {'status':'ok'}
            assert secure.get('/travel/api/trips', **opts).status_code == 401
            assert secure.get('/travel/', headers={'Host':'other.invalid'}, extensions=opts['extensions']).status_code == 421
            if mode=='dns':
                with pytest.raises(httpx.ConnectError):
                    secure.get('/travel/', extensions={'sni_hostname':'wrong.fixture.test'})
        with nginx(scratch/'maintenance', host, mode, backend.base_url.port, maintenance=True) as (plain, secure):
            assert plain.get('/').text == 'OTHER SITE'
            for path in ('/travel', '/travel/trips/1', '/travel/api/health'):
                reply = secure.get(path, **opts)
                assert reply.status_code == 503 and reply.headers['retry-after'] == '60' and reply.headers['cache-control']=='no-store'
    assert verify(candidate) == before


def test_standalone_edge_replaces_client_headers_and_uvicorn_rejects_untrusted_peer(scratch):
    if not fixture.NGINX.is_file():
        pytest.skip('Portable nginx is required')
    candidate = REVIEW/'application'
    baseline = verify(candidate)
    host = 'travel.fixture.test'
    forged = {'X-Forwarded-For':'203.0.113.8, 203.0.113.9', 'X-Forwarded-Proto':'http',
              'X-Forwarded-Host':'other.invalid', 'Forwarded':'for=203.0.113.9;proto=http'}
    probe = '/api/__isolated_forward_probe'
    with packaged_backend(candidate,scratch/'data',scratch/'proof.json',shared=False,proxy_headers=True,
                          forward_probe=True) as (backend, _):
        with nginx(scratch/'edge',host,'dns',backend.base_url.port,maintenance=False,standalone=True) as (_, secure):
            # 127.0.0.1 stands in for the exact allowlisted nginx peer. Direct
            # 127.0.0.2 is still a different TCP peer even on the same subnet.
            with httpx.Client(transport=httpx.HTTPTransport(local_address='127.0.0.2', trust_env=False),
                              base_url=backend.base_url,timeout=4) as adjacent:
                direct = adjacent.get(probe,headers={**forged,'X-Forwarded-Proto':'https'})
                assert direct.status_code==200 and direct.json()=={'address':'127.0.0.2','scheme':'http','host':'127.0.0.1'}
            trusted = secure.get('/travel'+probe,headers={**forged,'Host':host},extensions={'sni_hostname':host})
            assert trusted.status_code==200 and trusted.json()=={'address':'127.0.0.1','scheme':'https','host':host}
            assert secure.get('/travel'+probe,headers={'Host':'other.invalid'},extensions={'sni_hostname':host}).status_code==421
    assert verify(candidate)==baseline


def test_current_shared_http_rule_serves_exact_challenge_bytes_without_new_location(scratch):
    if not fixture.NGINX.is_file():
        pytest.skip('Portable nginx is required')
    # The TLS half is just a sandbox listener; the 80 rule here is copied from
    # the hash-checked old configuration without the new ACME location.
    with nginx(scratch/'existing', '134.175.137.18','ip',9,maintenance=True,standalone=True,legacy_challenge=True) as (plain, _):
        assert plain.get('/').text == 'OTHER SITE FALLBACK'
        exact = plain.get('/.well-known/acme-challenge/fixture-token')
        wrong = plain.get('/.well-known/acme-challenge/absent')
        assert exact.status_code == 200 and exact.content == b'challenge-payload'
        assert wrong.status_code == 200 and wrong.content != exact.content
