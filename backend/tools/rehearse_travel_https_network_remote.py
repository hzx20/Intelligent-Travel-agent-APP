"""Temporary Linux bridge and nginx fixture for the confirmed public IP entry."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time


DOCKER = ('sudo', '-n', '/usr/bin/docker')
NETWORK = 'travel-edge'
SUBNET = '172.31.240.0/28'
GATEWAY = '172.31.240.1'
PROXY = '172.31.240.2'
DYNAMIC = '172.31.240.8/29'
INTERFACE = 'br-travel-edge'
PUBLIC_IP = '134.175.137.18'
LABEL = 'travel.edge.network-rehearsal'
PHASE = 'preflight'


def require(condition):
    if not condition:
        raise ValueError('Isolated network check failed')


def command(*args):
    return subprocess.run([str(arg) for arg in args], check=True, capture_output=True,
                          stdin=subprocess.DEVNULL, timeout=12).stdout


def owned(resource, tag):
    args = ('network', 'ls') if resource == 'network' else ('ps', '-a')
    ids = command(*DOCKER, *args, '--no-trunc', '--filter', f'label={LABEL}={tag}', '-q').decode('ascii').split()
    require(len(ids) <= 1 and all(re.fullmatch('[a-f0-9]{64}', obj) for obj in ids))
    return ids


@contextmanager
def bridge(tag):
    try:
        network = command(*DOCKER, 'network', 'create', '--driver', 'bridge',
                          '--opt', f'com.docker.network.bridge.name={INTERFACE}',
                          '--subnet', SUBNET, '--gateway', GATEWAY, '--ip-range', DYNAMIC,
                          '--label', f'{LABEL}={tag}', NETWORK).decode('ascii').strip()
        require(owned('network', tag) == [network])
        data = json.loads(command(*DOCKER, 'network', 'inspect', network))[0]
        require(data['Name'] == NETWORK and data['Driver'] == 'bridge'
                and data['Options']['com.docker.network.bridge.name'] == INTERFACE and not data['Containers'])
        plan, = data['IPAM']['Config']
        require((plan['Subnet'], plan['Gateway'], plan['IPRange']) == (SUBNET, GATEWAY, DYNAMIC))
        yield
    finally:
        for network in owned('network', tag):
            command(*DOCKER, 'network', 'rm', network)
        require(not owned('network', tag))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append((self.client_address[0], self.path))
        payload = '|'.join((self.client_address[0], self.path, self.headers.get('X-Forwarded-Proto', ''),
                            self.headers.get('X-Real-IP', ''))).encode('ascii')
        self.send_response(200)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class PrivateServer(ThreadingHTTPServer):
    def verify_request(self, request, address):
        return address[0] == PROXY


@contextmanager
def backend():
    with PrivateServer((GATEWAY, 0), Handler) as server:
        server.requests = []
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield server
        finally:
            server.shutdown()
            worker.join(timeout=3)


@contextmanager
def container(tag, work, image, config):
    name = 'travel-edge-network-qa-' + tag
    try:
        container_id = command(*DOCKER, 'run', '--pull', 'never', '--detach', '--rm',
                               '--label', f'{LABEL}={tag}', '--name', name, '--network', NETWORK,
                               '--ip', PROXY, '--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=24m,mode=1777',
                               '--memory', '160m', '--memory-swap', '160m', '--cpus', '0.25', '--pids-limit', '32',
                               '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                               '--user', f'{os.getuid()}:{os.getgid()}',
                               '--mount', f'type=bind,source={work},target=/review,readonly', '--entrypoint', 'nginx',
                               image, '-c', f'/review/{config}', '-g', 'daemon off;').decode('ascii').strip()
        require(owned('container', tag) == [container_id])
        state, = json.loads(command(*DOCKER, 'inspect', '--type', 'container', container_id))
        networks = state['NetworkSettings']['Networks']
        require(state['State']['Running'] and not state['HostConfig']['PortBindings']
                and set(networks) == {NETWORK} and networks[NETWORK]['IPAddress'] == PROXY)
        command(*DOCKER, 'exec', container_id, '/usr/sbin/nginx', '-t', '-c', f'/review/{config}')
        yield container_id
    finally:
        for container_id in owned('container', tag):
            command(*DOCKER, 'rm', '-f', container_id)
        require(not owned('container', tag))


def get(container_id, path, *options):
    for attempt in range(6):
        try:
            return command(*DOCKER, 'exec', container_id, '/usr/bin/curl', '-q', '--noproxy', '*', '-sS',
                           '--max-time', '2', '--connect-to', f'{PUBLIC_IP}:24443:127.0.0.1:24443',
                           '--cacert', '/review/cert.pem', *options, f'https://{PUBLIC_IP}:24443{path}')
        except subprocess.CalledProcessError:
            if attempt == 5:
                raise
            time.sleep(.2)


def configs(work, data, port):
    for name in ('normal.conf', 'maintenance.conf'):
        config = data[name]
        require(config.count('listen 443 ssl default_server;') == 1)
        require(config.count(f'http://{GATEWAY}:8000/') == (2 if name == 'normal.conf' else 0))
        config = (config.replace('listen 443 ssl default_server;', 'listen 127.0.0.1:24443 ssl default_server;')
                  .replace('/usr/share/nginx/html', '/review/site')
                  .replace('/etc/nginx/travel-tls/current/fullchain.pem', '/review/cert.pem')
                  .replace('/etc/nginx/travel-tls/current/privkey.pem', '/review/key.pem')
                  .replace(f'http://{GATEWAY}:8000/', f'http://{GATEWAY}:{port}/'))
        (work / name).write_text(config, encoding='utf-8')
    site = work / 'site/travel'
    site.mkdir(parents=True)
    (site / 'index.html').write_text('isolated-travel-edge', encoding='ascii')


def run(tag, payload):
    global PHASE
    require(re.fullmatch('[0-9a-f]{16}', tag) and os.geteuid() != 0 and sys.flags.isolated)
    os.umask(0o077)
    host_inventory = {'__name__': 'network_inventory'}
    probe = {'__name__': 'http_probe'}
    exec(compile(payload['host.py'], '<host-inventory>', 'exec'), host_inventory)
    exec(compile(payload['webroot.py'], '<webroot-preflight>', 'exec'), probe)
    before = host_inventory['inventory'](probe)
    candidate = before['network_candidate']
    require(all(candidate[key] is False for key in ('overlaps_observed_routes', 'overlaps_observed_docker_ipam',
                                                    'docker_name_in_use', 'interface_name_in_use')))
    require((candidate['subnet'], candidate['gateway'], candidate['proxy_ip'], candidate['dynamic_range'],
             candidate['docker_network'], candidate['host_interface']) == (SUBNET, GATEWAY, PROXY, DYNAMIC, NETWORK, INTERFACE))
    image = command(*DOCKER, 'inspect', '--format', '{{.Image}}', 'continew-web').decode('ascii').strip()
    require(re.fullmatch('sha256:[a-f0-9]{64}', image))
    require(command(*DOCKER, 'image', 'inspect', '--format', '{{json .Config.Volumes}}', image).strip() in (b'null', b'{}'))

    PHASE = 'bridge'
    with bridge(tag), tempfile.TemporaryDirectory(prefix='travel-edge-network-'+tag+'-', dir='/tmp') as scratch, backend() as server:
        work = Path(scratch)
        PHASE = 'synthetic_certificate'
        command('/usr/bin/openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                '-subj', '/CN=Travel dedicated bridge rehearsal', '-addext', 'subjectAltName=IP:'+PUBLIC_IP,
                '-addext', 'extendedKeyUsage=serverAuth', '-keyout', work/'key.pem', '-out', work/'cert.pem')
        configs(work, payload, server.server_port)
        PHASE = 'maintenance'
        with container(tag, work, image, 'maintenance.conf') as maintenance:
            require(get(maintenance, '/travel/api/health', '-o', '/dev/null', '-w', '%{http_code}') == b'503')
            require(not server.requests)
        PHASE = 'normal'
        with container(tag, work, image, 'normal.conf') as normal:
            require(maintenance != normal)
            require(get(normal, '/travel/trips/25') == b'isolated-travel-edge')
            response = get(normal, '/travel/api/health', '-H', 'X-Forwarded-Proto: http', '-H', 'X-Real-IP: 192.0.2.3')
            require(response.decode('ascii') == f'{PROXY}|/api/health|https|127.0.0.1')
            require(server.requests == [(PROXY, '/api/health')])

    PHASE = 'cleanup'
    require(not work.exists())
    require(host_inventory['inventory'](probe) == before)
    return {'state': 'dedicated_bridge_verified', 'fixed_ip_after_recreation': True,
            'maintenance_503': True, 'private_gateway_forwarding': True, 'trusted_synthetic_tls': True,
            'no_published_ports': True, 'cleanup_verified': True}


def interrupted(*args):
    raise RuntimeError('Rehearsal interrupted')


if __name__ == '__main__':
    try:
        for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, interrupted)
        print(json.dumps(run(sys.argv[1], json.loads(sys.stdin.buffer.read(32 * 1024)))))
    except (IndexError, KeyError, OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({'state': 'not_verified', 'phase': PHASE, 'error_type': type(error).__name__}), flush=True)
        raise SystemExit(1) from None
