"""Prove maintenance suppresses all travel methods despite shared error pages."""
import subprocess
import time

import httpx
import pytest

from test_release_runtime import NGINX, port
from tools.proxy_chain_fixture import render_fixture
from tools.travel_maintenance import MESSAGE, render

pytestmark = pytest.mark.skipif(not NGINX.is_file(), reason='Explicit portable nginx required')


def test_shared_nginx_maintenance_returns_503_for_all_travel_methods(tmp_path):
    prefix = tmp_path / 'maintenance'
    prefix.mkdir()
    (prefix / 'temp').mkdir()
    source = '''events { worker_connections 64; }
http { server { listen 80; root /usr/share/nginx/html;
recursive_error_pages on;
error_page 500 502 503 504 =200 /shared-error;
location = /shared-error { return 200 "OTHER-ERROR-PAGE"; }
location = / { return 200 "OTHER-SITE"; }
location /api/ { return 202 "OTHER-API"; }
location ^~ /travel/ { return 200 "ORIGINAL-TRAVEL"; }
} }
'''
    maintenance, _ = render(source)
    listen = port()
    memory = '/dev/shm/travel-proxy-chain-' + 'b' * 32
    fixture = render_fixture(maintenance, memory, '127.0.0.1', listen, '127.0.0.1', port())
    fixture = fixture.replace(memory, prefix.as_posix())
    (prefix / 'nginx.conf').write_text(fixture, encoding='utf-8')
    args = [str(NGINX), '-p', prefix.as_posix() + '/', '-c', 'nginx.conf']
    process = None
    try:
        checked = subprocess.run([*args, '-t'], capture_output=True, text=True, timeout=10,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        assert checked.returncode == 0, checked.stdout + checked.stderr
        process = subprocess.Popen(args, cwd=prefix, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        with httpx.Client(base_url=f'http://127.0.0.1:{listen}', trust_env=False, timeout=5) as client:
            deadline = time.monotonic() + 10
            while True:
                assert process.poll() is None
                try:
                    if client.get('/').status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert time.monotonic() < deadline
                time.sleep(.05)
            assert client.get('/').text == 'OTHER-SITE'
            assert client.post('/api/shared').status_code == 202
            assert client.get('/shared-error').text == 'OTHER-ERROR-PAGE'
            for method in ('GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'):
                for path in ('/travel', '/travel/', '/travel/trips/1', '/travel/assets/main.js',
                             '/travel/api/trips/create', '/travel/api/plan/stream'):
                    response = client.request(method, path)
                    assert response.status_code == 503, (method, path, response.status_code)
                    assert response.headers['retry-after'] == '60'
                    assert response.headers['cache-control'] == 'no-store'
                    if method != 'HEAD':
                        assert response.content.decode('utf-8') == MESSAGE
            assert client.get('/travel/__maintenance_response').status_code == 404
    finally:
        if process is not None:
            if process.poll() is None:
                subprocess.run([*args, '-s', 'quit'], capture_output=True, timeout=10,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=10)
