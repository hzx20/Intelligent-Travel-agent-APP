from http.server import ThreadingHTTPServer
import os
import shutil
import subprocess
from threading import Thread
import time

import httpx
import pytest

from test_nginx_merge_runtime import Backend
from test_release_runtime import NGINX, DIST, port
from tools.nginx_merge import merge
from tools.proxy_chain_fixture import render_fixture
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(not NGINX.is_file() or not (DIST / '.vite/manifest.json').is_file(), reason='Explicit nginx and build required')


def test_fixture_config_runs_with_isolated_prefix_and_proxy(tmp_path):
    prefix = tmp_path / 'fixture'
    prefix.mkdir()
    (prefix / 'temp').mkdir()
    shutil.copyfile(NGINX.parent / 'conf/mime.types', prefix / 'mime.types')
    shutil.copytree(DIST, prefix / 'html/travel')
    backend = ThreadingHTTPServer(('127.0.0.1', 0), Backend)
    thread = Thread(target=backend.serve_forever, daemon=True)
    thread.start()
    listen = port()
    source = '''events { worker_connections 64; }
http { include mime.types; server { listen 80; root /usr/share/nginx/html;
location = / { return 200 "OTHER-SITE"; }
location ^~ /travel/ { try_files $uri $uri/ /travel/index.html; }
} }
'''
    merged, _ = merge(source, (ROOT / 'deploy/nginx-travel.conf').read_text(encoding='utf-8'))
    memory = '/dev/shm/travel-proxy-chain-' + 'a' * 32
    fixture = render_fixture(merged, memory, '127.0.0.1', listen, '127.0.0.1', backend.server_port)
    # The same rendered structure uses an existing private prefix on Windows.
    fixture = fixture.replace(memory, prefix.as_posix())
    (prefix / 'nginx.conf').write_text(fixture, encoding='utf-8')
    args = [str(NGINX), '-p', prefix.as_posix() + '/', '-c', 'nginx.conf']
    process = None
    try:
        checked = subprocess.run([*args, '-t'], capture_output=True, text=True, timeout=10,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        assert checked.returncode == 0, checked.stdout + checked.stderr
        process = subprocess.Popen(args, cwd=prefix, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        with httpx.Client(base_url=f'http://127.0.0.1:{listen}', trust_env=False, timeout=5) as client:
            for _ in range(100):
                assert process.poll() is None
                try:
                    if client.get('/').status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(.05)
            else:
                raise AssertionError('Fixture nginx did not start')
            assert client.get('/').text == 'OTHER-SITE'
            assert client.get('/travel/trips/123').content == (DIST / 'index.html').read_bytes()
            assert client.get('/travel/assets/missing.js').status_code == 404
            assert client.get('/travel/.vite/manifest.json').status_code == 404
            data = client.get('/travel/api/health', headers={'X-Forwarded-Proto': 'https'}).json()
            assert data['path'] == '/api/health' and data['proto'] == 'http'
    finally:
        try:
            if process is not None and process.poll() is None:
                subprocess.run([*args, '-s', 'quit'], capture_output=True, timeout=10,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        finally:
            if process is not None:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=10)
            backend.shutdown(); backend.server_close(); thread.join(timeout=5)
