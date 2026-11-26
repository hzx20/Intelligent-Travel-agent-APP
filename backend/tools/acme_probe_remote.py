"""Create and remove one synthetic public ACME probe file on the reviewed Linux host."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys


WEB = Path('/opt/continew/web')
CONF = Path('/opt/continew/nginx.conf')
CONF_HASH = '300ba0f22714e4942ac5e7ddeb6d51269f0f6b4557e78c3364e04ff50e589f73'
DOCKER = ('sudo', '-n', '/usr/bin/docker')


def require(ok):
    if not ok:
        raise RuntimeError('Unsafe or changed host state')


def show(*args):
    return subprocess.check_output(args, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)


def preflight():
    require(os.geteuid() != 0 and sys.platform == 'linux' and sys.flags.isolated and sys.dont_write_bytecode)
    require(WEB.resolve() == WEB and CONF.resolve() == CONF)
    require(hashlib.sha256(CONF.read_bytes()).hexdigest() == CONF_HASH)
    mounts = json.loads(show(*DOCKER, 'inspect', '--format', '{{json .Mounts}}', 'continew-web'))
    publication = json.loads(show(*DOCKER, 'inspect', '--format', '{{json (index .NetworkSettings.Ports "80/tcp")}}', 'continew-web'))
    config = [m for m in mounts if m['Destination'] == '/etc/nginx/nginx.conf']
    static = [m for m in mounts if m['Destination'] == '/usr/share/nginx/html']
    require(len(config) == len(static) == 1 and all(m[0]['Type'] == 'bind' and m[0]['RW'] is False for m in (config, static)))
    require(config[0]['Source'] == str(CONF) and static[0]['Source'] == str(WEB))
    require(any(m['HostIp'] == '0.0.0.0' and m['HostPort'] == '80' for m in publication or ()))
    container_hash = show(*DOCKER, 'exec', 'continew-web', '/usr/bin/sha256sum', '/etc/nginx/nginx.conf').split()[0].decode('ascii')
    require(container_hash == CONF_HASH)


def identity(info):
    return info.st_dev, info.st_ino


def remove_owned(parent, name, original, *, directory=False):
    try:
        require(identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == original)
        (os.rmdir if directory else os.unlink)(name, dir_fd=parent)
    except FileNotFoundError:
        return True
    return True


def probe(nonce):
    require(re.fullmatch('[a-f0-9]{24}', nonce))
    preflight()
    name = 'travel-acme-probe-' + nonce
    body = f'travel-acme-probe:{nonce}\n'.encode('ascii')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    cleanup_verified = True
    served = False
    with ExitStack() as handles:
        base = os.open(WEB, flags)
        handles.callback(os.close, base)
        made = []
        file_identity = None
        parent = base
        try:
            for directory in ('.well-known', 'acme-challenge'):
                try:
                    os.mkdir(directory, 0o755, dir_fd=parent)
                except FileExistsError:
                    created = False
                else:
                    created = True
                child = os.open(directory, flags, dir_fd=parent)
                handles.callback(os.close, child)
                if created:
                    made.append((parent, directory, identity(os.fstat(child))))
                    os.fchmod(child, 0o755)
                parent = child
            fd = os.open(name, os.O_WRONLY | os.O_NOFOLLOW | os.O_CREAT | os.O_EXCL, 0o644, dir_fd=parent)
            with os.fdopen(fd, 'wb') as file:
                file_identity = identity(os.fstat(file.fileno()))
                os.fchmod(file.fileno(), 0o644)
                file.write(body)
            print(json.dumps({'event': 'ready', 'name': name, 'body_sha256': hashlib.sha256(body).hexdigest()}), flush=True)
            ready, _, _ = select.select([sys.stdin], [], [], 15)
            if ready:
                served = sys.stdin.readline(32) == 'exact-match\n'
        finally:
            if file_identity:
                try:
                    remove_owned(parent, name, file_identity)
                except (OSError, RuntimeError):
                    cleanup_verified = False
            for parent, directory, original in reversed(made):
                try:
                    remove_owned(parent, directory, original, directory=True)
                except (OSError, RuntimeError):
                    cleanup_verified = False
            print(json.dumps({'event': 'cleanup', 'verified': cleanup_verified, 'caller_confirmed_exact_body': served}), flush=True)
    return 0 if cleanup_verified and served else 1


def interrupted(_signum, _frame):
    raise TimeoutError('Probe interrupted')


if __name__ == '__main__':
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, interrupted)
    try:
        raise SystemExit(probe(sys.argv[1]))
    except (OSError, RuntimeError, ValueError, TimeoutError, subprocess.SubprocessError) as error:
        print(json.dumps({'event': 'failed', 'error_type': type(error).__name__}), flush=True)
        raise SystemExit(1) from None
