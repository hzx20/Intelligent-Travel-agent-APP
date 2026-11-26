"""Linux-only disposable-container bind/reload rehearsal; never reload production."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time
import uuid

from tools.bind_switch_fixture import missing_entry_fixture, render_bind_fixture, required_hosts

DOCKER = ['sudo', '-n', 'docker']
SOURCE_HASH = '300ba0f22714e4942ac5e7ddeb6d51269f0f6b4557e78c3364e04ff50e589f73'


def command(args, **kwargs):
    return subprocess.run(args, capture_output=True, timeout=kwargs.pop('timeout', 10), **kwargs)


def digest(content):
    return hashlib.sha256(content).hexdigest()


def production_state():
    inspected = command([*DOCKER, 'inspect', 'continew-web'], text=True)
    assert inspected.returncode == 0
    meta = json.loads(inspected.stdout)[0]
    config = command([*DOCKER, 'exec', 'continew-web', 'sha256sum', '/etc/nginx/nginx.conf'], text=True)
    pid = command([*DOCKER, 'exec', 'continew-web', 'cat', '/var/run/nginx.pid'], text=True)
    backend = command(['systemctl', 'show', 'travel', '--property=MainPID', '--value'], text=True)
    assert config.returncode == pid.returncode == backend.returncode == 0
    return {'container_id': meta['Id'], 'image_id': meta['Image'], 'container_running': meta['State']['Running'],
            'host_config_sha256': digest(Path('/opt/continew/nginx.conf').read_bytes()),
            'container_config_sha256': config.stdout.split()[0], 'nginx_pid': pid.stdout.strip(),
            'backend_pid': backend.stdout.strip()}


@contextmanager
def locked_file(path):
    import fcntl
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield fd
    finally:
        os.close(fd)


def write_same_inode(path, fd, content, expected):
    info = os.fstat(fd)
    assert not path.is_symlink() and (info.st_dev, info.st_ino) == (path.stat().st_dev, path.stat().st_ino)
    assert digest(os.pread(fd, info.st_size, 0)) == expected
    offset = 0
    while offset < len(content):
        written = os.pwrite(fd, content[offset:], offset)
        assert written > 0
        offset += written
    os.ftruncate(fd, len(content)); os.fsync(fd)
    assert path.read_bytes() == content and path.stat().st_ino == info.st_ino


class Rehearsal:
    def __init__(self, root, expected):
        self.root, self.expected = root, expected
        self.nonce = uuid.uuid4().hex
        self.memory = '/dev/shm/travel-proxy-chain-' + self.nonce
        self.label = 'codex.travel.bind-run=' + self.nonce
        self.ids = set()
        self.work = root / 'work'
        self.config = self.work / 'nginx.conf'
        self.image = expected['image_id']

    def inspect_owned(self, cid):
        result = command([*DOCKER, 'inspect', cid], text=True)
        assert result.returncode == 0
        meta = json.loads(result.stdout)[0]
        assert meta['Id'] == cid and meta['Image'] == self.image
        assert meta['Config']['Labels'].get('codex.travel.bind-run') == self.nonce
        assert meta['Name'].startswith('/travel-bind-qa-' + self.nonce + '-')
        assert meta['HostConfig']['NetworkMode'] == 'none' and not meta['HostConfig']['PortBindings']
        assert meta['HostConfig']['ReadonlyRootfs'] and meta['HostConfig']['Memory'] == 128 * 1024 * 1024
        mounts = {m['Destination']: m for m in meta['Mounts'] if m['Type'] == 'bind'}
        assert set(mounts) == {'/etc/nginx/nginx.conf', '/usr/share/nginx/html'}
        assert mounts['/etc/nginx/nginx.conf']['Source'] == str(self.config)
        assert mounts['/usr/share/nginx/html']['Source'] == str(self.work / 'html')
        assert all(not m['RW'] for m in mounts.values())
        assert cid != self.expected['production_container_id']
        return meta

    def create(self, suffix, hosts):
        args = [*DOCKER, 'create', '--name', 'travel-bind-qa-' + self.nonce + '-' + suffix,
                '--label', self.label, '--network', 'none', '--read-only', '--memory', '128m', '--memory-swap', '128m',
                '--cpus', '0.25', '--pids-limit', '32', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '--user', str(os.getuid()) + ':' + str(os.getgid()), '--stop-signal', 'SIGQUIT',
                '--mount', 'type=bind,src=' + str(self.config) + ',dst=/etc/nginx/nginx.conf,readonly',
                '--mount', 'type=bind,src=' + str(self.work / 'html') + ',dst=/usr/share/nginx/html,readonly',
                '--entrypoint', '/usr/bin/sh']
        for host in hosts:
            args += ['--add-host', host + ':127.0.0.1']
        args += [self.image, '-c', 'umask 077; mkdir -p ' + self.memory + '/temp; exec /usr/sbin/nginx -p '
                 + self.memory + '/ -c /etc/nginx/nginx.conf']
        result = command(args)
        assert result.returncode == 0, 'Could not create isolated container'
        cid = result.stdout.decode().strip()
        assert re.fullmatch('[0-9a-f]{64}', cid)
        self.ids.add(cid); self.inspect_owned(cid)
        assert command([*DOCKER, 'start', cid]).returncode == 0
        self.wait_status(cid, '/', 200)
        return cid

    def execute(self, cid, args):
        assert cid in self.ids and cid != self.expected['production_container_id']
        return command([*DOCKER, 'exec', cid, *args])

    def http(self, cid, path, method='GET'):
        args = ['/usr/bin/curl', '--noproxy', '*', '--silent', '--show-error', '--max-time', '2',
                '--http1.1', '--dump-header', '-', '--output', '-']
        args += ['--head'] if method == 'HEAD' else ['--request', method]
        result = self.execute(cid, [*args, 'http://127.0.0.1:28080' + path])
        assert result.returncode == 0, 'Isolated HTTP probe failed'
        headers, _, body = result.stdout.partition(b'\r\n\r\n')
        lines = headers.decode('iso-8859-1').split('\r\n')
        return int(lines[0].split()[1]), {k.lower(): v.strip() for k, v in (line.split(':', 1) for line in lines[1:] if ':' in line)}, body

    def wait_status(self, cid, path, status):
        deadline = time.monotonic() + 10
        while True:
            try:
                if self.http(cid, path)[0] == status:
                    return
            except AssertionError:
                pass
            assert time.monotonic() < deadline, 'Isolated runtime did not reach expected state'
            time.sleep(.15)

    def visible_hash(self, cid):
        result = self.execute(cid, ['sha256sum', '/etc/nginx/nginx.conf'])
        assert result.returncode == 0
        return result.stdout.decode().split()[0]

    def syntax(self, cid):
        result = self.execute(cid, ['/usr/sbin/nginx', '-p', self.memory + '/', '-c', '/etc/nginx/nginx.conf', '-t'])
        return result.returncode, result.stderr.decode(errors='replace')

    def reload(self, cid):
        self.inspect_owned(cid)
        assert digest(self.config.read_bytes()) == self.visible_hash(cid), 'Bind visibility gate failed'
        assert self.syntax(cid)[0] == 0, 'Syntax gate failed'
        result = self.execute(cid, ['/usr/sbin/nginx', '-p', self.memory + '/', '-c', '/etc/nginx/nginx.conf', '-s', 'reload'])
        assert result.returncode == 0
        return result.returncode

    def normal(self, cid):
        self.wait_status(cid, '/travel/trips/1', 200)
        assert self.http(cid, '/')[2] == b'OTHER-SYNTHETIC-SITE'
        assert self.http(cid, '/travel/trips/1')[2] == b'TRAVEL-SYNTHETIC-SPA'
        assert self.http(cid, '/travel/api/health')[0::2] == (202, b'SYNTHETIC-BACKEND')
        assert self.http(cid, '/travel/assets/probe.js')[0] == 200
        assert self.http(cid, '/travel/assets/missing.js')[0] == 404

    def maintenance(self, cid, exhaustive=False):
        self.wait_status(cid, '/travel/', 503)
        methods = ('GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS') if exhaustive else ('GET',)
        paths = ('/travel', '/travel/', '/travel/trips/1', '/travel/assets/probe.js',
                 '/travel/api/trips/create', '/travel/api/plan/stream') if exhaustive else ('/travel/',)
        count = 0
        for method in methods:
            for path in paths:
                status, headers, body = self.http(cid, path, method)
                assert status == 503 and headers.get('cache-control') == 'no-store' and headers.get('retry-after') == '60'
                if method != 'HEAD':
                    assert body.decode('utf-8') == '旅行服务正在维护，请稍后重试。'
                count += 1
        assert self.http(cid, '/')[2] == b'OTHER-SYNTHETIC-SITE'
        return count

    def remove(self, cid):
        self.inspect_owned(cid)
        stopped = command([*DOCKER, 'stop', '--time', '3', cid])
        assert stopped.returncode == 0
        assert command([*DOCKER, 'rm', '--volumes', cid]).returncode == 0

    def cleanup(self):
        # Discover even a container created just before a CLI observation timeout.
        listed = command([*DOCKER, 'ps', '-aq', '--no-trunc', '--filter', 'label=' + self.label], text=True)
        assert listed.returncode == 0
        for cid in listed.stdout.split():
            self.inspect_owned(cid)
            self.ids.add(cid)
            stopped = command([*DOCKER, 'stop', '--time', '3', cid])
            assert stopped.returncode == 0
            assert command([*DOCKER, 'rm', '--volumes', cid]).returncode == 0
        final = command([*DOCKER, 'ps', '-aq', '--no-trunc', '--filter', 'label=' + self.label], text=True)
        return final.returncode == 0 and not final.stdout.strip()


def exact_syntax(root, maintenance):
    path = '/dev/shm/travel-maintenance-check-' + uuid.uuid4().hex
    assert re.fullmatch('/dev/shm/travel-maintenance-check-[0-9a-f]{32}', path)
    (root / 'exact-syntax-request.json').write_text(json.dumps({'ram_path': path}))
    script = r'''set -eu
umask 077
mkdir -- "$1"
cleanup() { rm -f -- "$1/nginx.conf" "$1/mime.types"; rmdir -- "$1"; test ! -e "$1"; printf '\nCHECK_RAM_REMOVED\n'; }
trap 'cleanup "$1"' EXIT
cat > "$1/nginx.conf"
ln -s /etc/nginx/mime.types "$1/mime.types"
nginx -t -c "$1/nginx.conf"
'''
    checked = command([*DOCKER, 'exec', '-i', 'continew-web', 'sh', '-c', script, 'check', path], input=maintenance, timeout=20)
    return {'exit_code': checked.returncode, 'output': checked.stderr.decode(errors='replace'),
            'ram_copy_removed': b'CHECK_RAM_REMOVED' in checked.stdout}


def run(root):
    root = Path(root).absolute()
    assert root.resolve() == root and re.fullmatch(r'/tmp/travel-release-rehearsal-\d{8}-[0-9a-f]{8}', str(root.parent))
    assert root.stat().st_uid == os.getuid() and not root.stat().st_mode & 0o077
    expected = json.loads((root / 'expected.json').read_text())
    for name, wanted in expected['files'].items():
        assert (root / name).resolve().is_relative_to(root) and digest((root / name).read_bytes()) == wanted
    assert shutil.disk_usage(root).free >= 100 * 1024 * 1024
    available = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
    assert available >= 300 * 1024
    before = production_state()
    assert before['host_config_sha256'] == before['container_config_sha256'] == SOURCE_HASH
    assert before['container_id'] == expected['production_container_id'] and before['image_id'] == expected['image_id']
    own = Rehearsal(root, expected)
    normal_source = (root / 'normal.conf').read_text()
    maintenance_source = (root / 'maintenance.conf').read_text()
    normal = render_bind_fixture(normal_source, own.memory).encode()
    maintenance = render_bind_fixture(maintenance_source, own.memory).encode()
    hosts = sorted(set(required_hosts(normal_source)) | set(required_hosts(maintenance_source)))
    report = {'success': False, 'production_deployed': False, 'production_reloaded': False,
              'production_database_accessed': False, 'isolated_network': 'none', 'published_ports': False,
              'image_id': own.image, 'upstream_hostname_count': len(hosts),
              'normal_source_sha256': digest((root / 'normal.conf').read_bytes()),
              'maintenance_source_sha256': digest((root / 'maintenance.conf').read_bytes()),
              'fixture_normal_sha256': digest(normal), 'fixture_maintenance_sha256': digest(maintenance)}
    started = time.monotonic()
    try:
        report['exact_maintenance_syntax'] = exact_syntax(root, (root / 'maintenance.conf').read_bytes())
        assert report['exact_maintenance_syntax']['exit_code'] == 0 and report['exact_maintenance_syntax']['ram_copy_removed']
        own.work.mkdir(mode=0o700)
        (own.work / 'html/travel/assets').mkdir(parents=True)
        (own.work / 'html/index.html').write_bytes(b'OTHER-SYNTHETIC-SITE')
        (own.work / 'html/travel/index.html').write_bytes(b'TRAVEL-SYNTHETIC-SPA')
        (own.work / 'html/travel/assets/probe.js').write_bytes(b'window.syntheticProbe=true;')
        own.config.write_bytes(normal)
        cid = own.create('a', hosts); own.normal(cid)
        inode = own.config.stat().st_ino
        replacement = own.work / 'replacement.conf'; replacement.write_bytes(maintenance)
        os.replace(replacement, own.config)
        assert own.config.stat().st_ino != inode
        assert digest(own.config.read_bytes()) == digest(maintenance) and own.visible_hash(cid) == digest(normal)
        try:
            own.reload(cid)
        except AssertionError as error:
            assert 'visibility gate' in str(error)
        else:
            raise AssertionError('Visibility gate did not refuse stale bind')
        own.normal(cid)
        report['atomic_replace_stale_bind_reproduced'] = True
        report['visibility_gate_prevented_wrong_reload'] = True
        own.remove(cid)
        with locked_file(own.config) as fd:
            write_same_inode(own.config, fd, normal, digest(maintenance))
        cid = own.create('b', hosts); own.normal(cid)
        with locked_file(own.config) as fd:
            write_same_inode(own.config, fd, maintenance, digest(normal))
            own.reload(cid)
            report['maintenance_http_combinations'] = own.maintenance(cid, exhaustive=True)
            broken = maintenance + b'\nBROKEN {;\n'
            write_same_inode(own.config, fd, broken, digest(maintenance))
            assert own.visible_hash(cid) == digest(broken)
            syntax_exit, _ = own.syntax(cid)
            assert syntax_exit != 0
            try:
                own.reload(cid)
            except AssertionError as error:
                assert 'Syntax gate' in str(error)
            else:
                raise AssertionError('Syntax gate did not refuse malformed config')
            own.maintenance(cid)
            write_same_inode(own.config, fd, maintenance, digest(broken))
            assert own.syntax(cid)[0] == 0
            report['malformed_candidate_blocked_and_same_inode_restored'] = True
            unbindable = normal.replace(b'listen 127.0.0.1:28080;', b'listen 192.0.2.123:28080;')
            assert unbindable != normal
            write_same_inode(own.config, fd, unbindable, digest(maintenance))
            bind_exit, bind_output = own.syntax(cid)
            assert bind_exit != 0 and 'bind() to 192.0.2.123:28080 failed' in bind_output
            try:
                own.reload(cid)
            except AssertionError as error:
                assert 'Syntax gate' in str(error)
            else:
                raise AssertionError('Unavailable listener should be blocked by this image configuration test')
            own.maintenance(cid)
            write_same_inode(own.config, fd, maintenance, digest(unbindable))
            report['unavailable_listener_blocked_by_configuration_test'] = {'exit_code': bind_exit, 'output': bind_output}
            # Syntax and a successful signal alone do not prove correct HTTP behavior.
            missing_entry = missing_entry_fixture(normal.decode()).encode()
            write_same_inode(own.config, fd, missing_entry, digest(maintenance))
            assert own.syntax(cid)[0] == 0
            signal_exit = own.reload(cid)
            own.wait_status(cid, '/travel/trips/1', 404)
            assert own.http(cid, '/')[2] == b'OTHER-SYNTHETIC-SITE'
            report['reload_exit_zero_but_http_gate_failure_detected'] = {'signal_exit': signal_exit, 'travel_entry_status': 404}
            write_same_inode(own.config, fd, maintenance, digest(missing_entry))
            own.reload(cid); own.maintenance(cid)
            report['failed_runtime_restored_maintenance'] = True
            write_same_inode(own.config, fd, normal, digest(maintenance))
            own.reload(cid); own.normal(cid)
            report['normal_configuration_restored_after_maintenance'] = True
        report['success'] = True
    finally:
        signal.signal(signal.SIGALRM, signal.SIG_IGN); signal.alarm(0)
        signal.signal(signal.SIGHUP, signal.SIG_IGN); signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            report['own_containers_removed'] = own.cleanup()
        except Exception as error:
            report['own_containers_removed'] = False
            report['cleanup_error'] = type(error).__name__
        if report['own_containers_removed'] and own.work.exists():
            assert own.work.resolve().parent == root
            shutil.rmtree(own.work)
        report['synthetic_work_removed'] = not own.work.exists()
        request_path = root / 'exact-syntax-request.json'
        if request_path.exists():
            ram_path = json.loads(request_path.read_text())['ram_path']
            assert re.fullmatch('/dev/shm/travel-maintenance-check-[0-9a-f]{32}', ram_path)
            report['exact_syntax_ram_absent'] = command([*DOCKER, 'exec', 'continew-web', 'test', '!', '-e', ram_path]).returncode == 0
        report['production_state_unchanged'] = production_state() == before
        report['elapsed_seconds'] = round(time.monotonic() - started, 2)
        (root / 'result.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report), flush=True)
    assert report['success'] and report['own_containers_removed'] and report['synthetic_work_removed'] and report['production_state_unchanged'] and report['exact_syntax_ram_absent']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review-dir', required=True)
    args = parser.parse_args()
    def interrupted(signum, frame):
        raise InterruptedError('Isolated bind rehearsal interrupted')
    signal.signal(signal.SIGHUP, interrupted); signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGALRM, interrupted); signal.alarm(180)
    run(args.review_dir)
