"""Exercise a merged shared config against a synthetic backend and actual dist."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
from threading import Thread
import time

import httpx
import pytest

from test_release_runtime import NGINX, DIST, port
from tools.nginx_merge import merge

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(not NGINX.is_file() or not (DIST / '.vite/manifest.json').is_file(),
                              reason='Explicit nginx binary and production build required')


class Backend(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({'path': self.path, 'xff': self.headers.get('X-Forwarded-For'),
                           'real_ip': self.headers.get('X-Real-IP'), 'proto': self.headers.get('X-Forwarded-Proto')}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_merged_shared_config_keeps_other_site_and_serves_travel(tmp_path):
    prefix = tmp_path / 'nginx'
    prefix.mkdir()
    for name in ('logs', 'temp', 'html'):
        (prefix / name).mkdir()
    shutil.copyfile(NGINX.parent / 'conf/mime.types', prefix / 'mime.types')
    shutil.copytree(DIST, prefix / 'html/travel')
    private = prefix / 'html/travel/.travel-deploy'
    private.mkdir()
    (private / 'record.json').write_text('PRIVATE-SYNTHETIC-RECORD')
    backend = ThreadingHTTPServer(('127.0.0.1', 0), Backend)
    thread = Thread(target=backend.serve_forever, daemon=True)
    thread.start()
    listen = port()
    source = f'''worker_processes 1;
daemon off;
master_process off;
pid logs/nginx.pid;
error_log logs/error.log;
events {{ worker_connections 128; }}
http {{
  include mime.types;
  default_type application/octet-stream;
  limit_req_zone $binary_remote_addr zone=travel_plan:10m rate=6r/m;
  map $http_upgrade $connection_upgrade {{ default upgrade; '' close; }}
  server {{
    listen 127.0.0.1:{listen}; root html; index index.html;
    location = / {{ return 200 "OTHER-SITE"; }}
    location /other-api/ {{ return 200 "OTHER-API"; }}
    location ~* \\.(js|css)$ {{ return 418; }}
    location ^~ /travel/api/ {{ proxy_pass http://127.0.0.1:{backend.server_port}/api/; }}
    location ^~ /travel/ {{ try_files $uri $uri/ /travel/index.html; }}
    location = /travel {{ return 301 /travel/; }}
  }}
}}
'''
    template = (ROOT / 'deploy/nginx-travel.conf').read_text(encoding='utf-8').replace('172.18.0.1:8000', f'127.0.0.1:{backend.server_port}')
    config, report = merge(source, template)
    assert report['unrelated_directives_unchanged']
    (prefix / 'nginx.conf').write_text(config, encoding='utf-8')
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
                assert process.poll() is None, 'Isolated nginx exited early'
                try:
                    if client.get('/').status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(.05)
            else:
                raise AssertionError('Isolated nginx startup timed out')
            assert client.get('/').text == 'OTHER-SITE'
            assert client.get('/other-api/anything').text == 'OTHER-API'
            assert client.get('/other.js').status_code == 418
            assert client.get('/travel', follow_redirects=False).status_code == 301
            deep = client.get('/travel/trips/123?panel=assistant')
            assert deep.content == (DIST / 'index.html').read_bytes()
            assert 'no-store' in deep.headers.get('cache-control', '')
            assert client.get('/travel/assets/not-a-file.js').status_code == 404
            assert client.get('/travel/.vite/manifest.json').status_code == 404
            assert client.get('/travel/.travel-deploy/record.json').status_code == 404
            manifest = json.loads((DIST / '.vite/manifest.json').read_text(encoding='utf-8'))
            for item in manifest.values():
                for name in (item['file'], *item.get('css', []), *item.get('assets', [])):
                    response = client.get('/travel/' + name)
                    assert response.status_code == 200 and 'immutable' in response.headers.get('cache-control', '')
                    if name.endswith('.js'):
                        assert 'javascript' in response.headers['content-type']
            proxied = client.get('/travel/api/health?case=merge', headers={'X-Forwarded-For': '198.51.100.29', 'X-Real-IP': '203.0.113.20', 'X-Forwarded-Proto': 'https'})
            assert proxied.json() == {'path': '/api/health?case=merge', 'xff': '127.0.0.1', 'real_ip': '127.0.0.1', 'proto': 'http'}
            stream = client.get('/travel/api/plan/stream?case=merge', headers={'X-Forwarded-Proto': 'https'})
            assert stream.json()['path'] == '/api/plan/stream?case=merge'
            assert stream.json()['proto'] == 'http'
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
                    process.kill()
                    process.wait(timeout=10)
            backend.shutdown()
            backend.server_close()
            thread.join(timeout=5)
