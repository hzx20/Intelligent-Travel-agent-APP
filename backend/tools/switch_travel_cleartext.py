"""Apply or restore the reviewed travel routes on the shared nginx inode."""
import argparse
from contextlib import closing
import hashlib
from http.client import HTTPConnection, HTTPException
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time


CONFIG = Path('/opt/continew/nginx.conf')
CONTAINER_CONFIG = '/etc/nginx/nginx.conf'
LEGACY_SHA = '300ba0f22714e4942ac5e7ddeb6d51269f0f6b4557e78c3364e04ff50e589f73'
CANDIDATE_SHA = '8e489e8d949d0d8df92d8ae832a1f8b10a547e20d6ed5174b2c36941ff0648fc'
LEGACY_UNIT = Path('/etc/systemd/system/travel.service')
LEGACY_UNIT_SHA = '7ff4dc5f4c925f607f1b36ddf5b0fef6f228c22eb5c4dc0f5c8e2d3824496c07'
SYSTEMCTL = '/usr/bin/systemctl'
ERRORS = (OSError, ValueError, KeyError, TypeError, HTTPException, subprocess.SubprocessError)
SWITCH_ERRORS = (*ERRORS, KeyboardInterrupt)
PUBLIC = '134.175.137.18'
LIMIT = 65536
DOCKER = '/usr/bin/docker'
NGINX = '/usr/sbin/nginx'


def check(ok, message):
    if not ok:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def command(*args):
    return subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=8).stdout


def shared_master(container, image):
    meta, = json.loads(command(DOCKER, 'inspect', '--type', 'container', container))
    mounts = [mount for mount in meta['Mounts'] if mount['Destination'] == CONTAINER_CONFIG]
    check(meta['Id'] == container and meta['Image'] == image and meta['Name'] == '/continew-web'
          and meta['State']['Running'] is True and meta['State']['Restarting'] is False,
          'Shared proxy identity changed')
    check(len(mounts) == 1 and mounts[0]['Type'] == 'bind' and mounts[0]['Source'] == str(CONFIG)
          and mounts[0]['RW'] is False, 'Shared configuration mount changed')
    pid = command(DOCKER, 'exec', container, '/bin/cat', '/var/run/nginx.pid').strip()
    check(pid.isdigit() and int(pid) > 0, 'Shared master PID could not be confirmed')
    return pid


def visible_digest(container):
    result = command(DOCKER, 'exec', container, '/usr/bin/sha256sum', CONTAINER_CONFIG).split()
    check(len(result) == 2 and re.fullmatch(b'[a-f0-9]{64}', result[0]) is not None,
          'Container configuration checksum could not be confirmed')
    return result[0].decode('ascii')


def response(path, method='GET', *, body_hash=False):
    with closing(HTTPConnection('127.0.0.1', 80, timeout=3)) as client:
        client.request(method, path, headers={'Host': PUBLIC, 'Connection': 'close'})
        with client.getresponse() as received:
            digest = None
            if body_hash:
                data = received.read(LIMIT + 1)
                check(len(data) <= LIMIT, 'Shared root response exceeds evidence limit')
                digest = sha(data)
            return received.status, received.getheader('Location'), digest


def maintenance_edges():
    for path in ('/travel/', '/travel/api/health'):
        status = command('/usr/bin/curl', '-q', '--noproxy', '*', '--silent', '--show-error', '--max-time', '4',
                         '--connect-to', f'{PUBLIC}:443:127.0.0.1:443', '--output', '/dev/null',
                         '--write-out', '%{http_code}', f'https://{PUBLIC}{path}')
        check(status == b'503', 'Trusted HTTPS maintenance response is unavailable')


def original_routes(root):
    return (response('/', body_hash=True) == root and response('/travel/')[0] == 200
            and response('/travel/api/health')[0] == 200)


def redirect_routes(root):
    deep = '/travel/trips/1?panel=budget'
    expected = (308, f'https://{PUBLIC}{deep}')
    return (response('/', body_hash=True) == root and response(deep)[:2] == expected
            and response(deep, 'HEAD')[:2] == expected and response(deep, 'POST')[0] == 403
            and response('/travel/api/health')[0] == 403 and response('/travel/api/auth/login', 'POST')[0] == 403
            and response('/.well-known/acme-challenge/travel-cutover-missing-fixture')[0] == 404)


def confirm_routes(predicate, root):
    deadline = time.monotonic() + 9
    passed = 0
    while time.monotonic() < deadline:
        try:
            passed = passed + 1 if predicate(root) else 0
        except (OSError, HTTPException):
            passed = 0
        if passed == 2:
            return
        time.sleep(.15)
    raise ValueError('Shared nginx route confirmation failed')


def read_locked(path, fd):
    info = os.fstat(fd)
    at_path = path.lstat()
    check(stat.S_ISREG(info.st_mode) and stat.S_ISREG(at_path.st_mode)
          and (info.st_dev, info.st_ino) == (at_path.st_dev, at_path.st_ino)
          and 0 < info.st_size <= LIMIT, 'Shared nginx config inode or size changed')
    os.lseek(fd, 0, os.SEEK_SET)
    with os.fdopen(os.dup(fd), 'rb') as file:
        return file.read(LIMIT + 1)


def write_locked(path, fd, data, known):
    check(read_locked(path, fd) == known[0], 'Configuration bytes changed outside the operation')
    os.lseek(fd, 0, os.SEEK_SET)
    offset = 0
    while offset < len(data):
        written = os.write(fd, data[offset:])
        check(written > 0, 'Configuration write did not advance')
        end = offset + written
        known[0] = known[0][:offset] + data[offset:end] + known[0][end:]
        offset = end
    os.ftruncate(fd, len(data))
    known[0] = data
    os.fsync(fd)
    check(read_locked(path, fd) == data, 'Configuration fsync readback differs')


def private_backup(path, data):
    parent = path.parent
    metadata = parent.lstat()
    check(parent.resolve(strict=True) == parent and stat.S_ISDIR(metadata.st_mode)
          and metadata.st_uid == 0 and not metadata.st_mode & 0o077, 'Backup directory must be private and root owned')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
    fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def switch_locked(fd, candidate, container, image, backup, *, restore=False):
    before_sha, after_sha = (CANDIDATE_SHA, LEGACY_SHA) if restore else (LEGACY_SHA, CANDIDATE_SHA)
    before_routes, after_routes = (redirect_routes, original_routes) if restore else (original_routes, redirect_routes)
    check(sha(candidate) == after_sha, 'Unexpected cleartext candidate')
    original = read_locked(CONFIG, fd)
    check(sha(original) == before_sha and visible_digest(container) == before_sha, 'Current configuration differs')
    master = shared_master(container, image)
    root = response('/', body_hash=True)
    check(root[0] == 200 and before_routes(root), 'Current routes do not match the reviewed online state')
    maintenance_edges()
    private_backup(backup, original)
    known = [original]
    signaled = False

    def recheck(expected):
        check(sha(read_locked(CONFIG, fd)) == expected and visible_digest(container) == expected,
              'Host and container configuration bytes differ')
        check(shared_master(container, image) == master, 'Shared nginx master changed')

    def syntax(expected):
        recheck(expected)
        command(DOCKER, 'exec', container, NGINX, '-t', '-c', CONTAINER_CONFIG)

    def reload(expected):
        recheck(expected)
        command(DOCKER, 'exec', container, NGINX, '-s', 'reload', '-c', CONTAINER_CONFIG)

    try:
        write_locked(CONFIG, fd, candidate, known)
        syntax(after_sha)
        signaled = True
        reload(after_sha)
        confirm_routes(after_routes, root)
        maintenance_edges()
        check(shared_master(container, image) == master and visible_digest(container) == after_sha,
              'Shared proxy no longer matches the checked candidate')
    except SWITCH_ERRORS as error:
        try:
            write_locked(CONFIG, fd, original, known)
            syntax(before_sha)
            if signaled:
                reload(before_sha)
            confirm_routes(before_routes, root)
            state = 'redirect_retained' if restore else 'original_restored_and_verified'
            return {'state': state, 'error_type': type(error).__name__}
        except SWITCH_ERRORS as recovery:
            return {'state': 'restoration_unconfirmed', 'error_type': type(error).__name__,
                    'recovery_error_type': type(recovery).__name__}
    state = 'legacy_service_and_http_restored' if restore else 'redirect_active_and_verified'
    return {'state': state, 'config_sha256': after_sha, 'original_config_backup_sha256': before_sha,
            'shared_root_unchanged': True}


def legacy_service_state(attempt):
    check(not os.path.lexists(attempt / 'write-opening.json')
          and not os.path.lexists(LEGACY_UNIT.with_name('travel-backend-ingress.service')),
          'The rollback is only valid before upgrade and write opening')
    check(LEGACY_UNIT.resolve(strict=True) == LEGACY_UNIT and sha(LEGACY_UNIT.read_bytes()) == LEGACY_UNIT_SHA,
          'Legacy service unit changed')
    fields = 'FragmentPath,NeedDaemonReload,DropInPaths,WorkingDirectory,ActiveState,MainPID'
    raw = command(SYSTEMCTL, 'show', '--no-pager', '--property=' + fields, 'travel.service')
    state = dict(line.split('=', 1) for line in raw.decode().splitlines())
    check(state['FragmentPath'] == str(LEGACY_UNIT) and state['NeedDaemonReload'] == 'no' and not state['DropInPaths']
          and state['WorkingDirectory'] == '/opt/travel/app/backend', 'Legacy service definition differs')
    return state


def wait_for_legacy_readiness(attempt, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            raw = command('/usr/bin/curl', '-qfsS', '--noproxy', '*', '--max-time', '2', '--max-filesize', '4096',
                          'http://127.0.0.1:8000/api/health')
            healthy = json.loads(raw) == {'status': 'ok'}
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        else:
            if healthy:
                state = legacy_service_state(attempt)
                check(state['ActiveState'] == 'active' and int(state['MainPID']) > 0,
                      'Healthy response without the legacy service')
                return
        time.sleep(.25)
    raise ValueError('Legacy backend did not become ready within the rollback deadline')


def restore_before_upgrade(fd, original, container, image, backup):
    current = sha(read_locked(CONFIG, fd))
    check(current in (LEGACY_SHA, CANDIDATE_SHA) and visible_digest(container) == current,
          'Shared route configuration changed')
    shared_master(container, image)
    maintenance_edges()
    legacy_service_state(backup.parent)
    command(SYSTEMCTL, 'start', 'travel.service')
    wait_for_legacy_readiness(backup.parent)
    if current == CANDIDATE_SHA:
        return switch_locked(fd, original, container, image, backup, restore=True)
    root = response('/', body_hash=True)
    check(root[0] == 200, 'Shared root is unavailable')
    confirm_routes(original_routes, root)
    return {'state': 'legacy_service_and_http_restored', 'config_sha256': LEGACY_SHA,
            'shared_root_unchanged': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--shared-container', required=True)
    parser.add_argument('--shared-image', required=True)
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--restore-before-upgrade', action='store_true',
                        help='Start the verified old backend and restore HTTP using its original config in the same private window')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        check(re.fullmatch('[0-9a-f]{64}', args.shared_container) and
              re.fullmatch('sha256:[0-9a-f]{64}', args.shared_image), 'Use exact container and image IDs')
        with args.candidate.open('rb') as file:
            candidate = file.read(LIMIT + 1)
        expected_sha = LEGACY_SHA if args.restore_before_upgrade else CANDIDATE_SHA
        check(sha(candidate) == expected_sha, 'Unexpected cleartext candidate')
        if not args.apply:
            print(json.dumps({'state': 'review_only', 'expected_config_sha256': expected_sha}))
            return 0
        check(sys.platform == 'linux' and os.geteuid() == 0 and args.backup is not None and args.backup.is_absolute(),
              'Expected root, the approved Linux host and an absolute backup path')
        if args.restore_before_upgrade:
            directory = args.backup.parent
            metadata = directory.lstat()
            check(directory.resolve(strict=True) == directory and stat.S_ISDIR(metadata.st_mode)
                  and metadata.st_uid == 0 and not metadata.st_mode & 0o077
                  and args.candidate == directory / 'shared-nginx-before.conf'
                  and args.candidate.resolve(strict=True) == args.candidate,
                  'Use the original configuration in the same private root-owned window')
        import fcntl

        def interrupted(*_):
            raise InterruptedError('Cleartext cutover was interrupted')

        for name in (signal.SIGHUP, signal.SIGTERM):
            signal.signal(name, interrupted)
        fd = os.open(CONFIG, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            operation = restore_before_upgrade if args.restore_before_upgrade else switch_locked
            outcome = operation(fd, candidate, args.shared_container, args.shared_image, args.backup)
        finally:
            os.close(fd)
    except ERRORS as error:
        outcome = {'state': 'precondition_unconfirmed', 'error_type': type(error).__name__}
    print(json.dumps(outcome))
    success = 'legacy_service_and_http_restored' if args.restore_before_upgrade else 'redirect_active_and_verified'
    return int(outcome['state'] != success)


if __name__ == '__main__':
    raise SystemExit(main())
