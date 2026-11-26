"""One explicitly authorized staging certificate test using the inspected webroot."""
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
import sys

if __package__:
    from . import acme_probe_remote as host
else:
    import acme_probe_remote as host


TARGET = '134.175.137.18'
STAGING = 'https://acme-staging-v02.api.letsencrypt.org/directory'
CERTBOT = '/snap/bin/certbot'
CERT = 'travel-ip-staging'
PHASE = 'preflight'


def demand(condition):
    if not condition:
        raise RuntimeError('Staging check failed')


def command(*args, timeout=10):
    return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          timeout=timeout, check=True, env={'PATH':'/usr/bin:/bin:/snap/bin','LC_ALL':'C.UTF-8'}).stdout


def version(output):
    m = re.fullmatch(rb'certbot ([0-9]+)\.([0-9]+)\.([0-9]+)\s*', output)
    if not m or tuple(map(int, m.groups())) < (5, 4, 0):
        raise ValueError('Certbot 5.4+ IP webroot support required')
    return '.'.join(x.decode('ascii') for x in m.groups())


def certbot_call(work):
    return (CERTBOT, 'certonly', '--server', STAGING, '--preferred-profile', 'shortlived', '--webroot',
            '--webroot-path', str(host.WEB), '--ip-address', TARGET, '--cert-name', CERT, '--non-interactive',
            '--agree-tos', '--register-unsafely-without-email', '--no-directory-hooks', '--config', str(work/'empty.ini'),
            '--config-dir', str(work/'config'), '--work-dir', str(work/'work'), '--logs-dir', str(work/'logs'))


def cert_is_exact(output):
    lines = output.decode('ascii').strip().splitlines()
    return (len(lines) == 3 and lines[0].startswith('issuer=') and '(STAGING)' in lines[0] and
            lines[1].strip() in ('X509v3 Subject Alternative Name:', 'X509v3 Subject Alternative Name: critical') and
            lines[2].strip() == f'IP Address:{TARGET}')


@contextmanager
def prepared_webroot():
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as stack:
        opened = os.open(host.WEB, flags)
        stack.callback(os.close, opened)
        created = []
        before = None
        try:
            for leaf in ('.well-known', 'acme-challenge'):
                try:
                    os.mkdir(leaf, 0o755, dir_fd=opened)
                except FileExistsError:
                    fresh = False
                else:
                    fresh = True
                fd = os.open(leaf, flags, dir_fd=opened)
                stack.callback(os.close, fd)
                if fresh:
                    created.append((opened,leaf,host.identity(os.fstat(fd))))
                    os.fchmod(fd, 0o755)
                opened = fd
            before = set(os.listdir(opened))
            yield
        finally:
            verified = before is not None and set(os.listdir(opened)) == before
            for parent, name, identity in reversed(created):
                try:
                    host.remove_owned(parent, name, identity, directory=True)
                except (OSError, RuntimeError):
                    verified = False
            print(json.dumps({'event':'public_cleanup','verified':verified}), flush=True)
            demand(verified)


def mark(state):
    global PHASE
    PHASE = state
    print(json.dumps({'event':'phase','name':state}), flush=True)


def installed_change_done(change, data):
    return (isinstance(change,str) and re.fullmatch('[0-9]{1,12}', change) and
            any(re.fullmatch(rb'\s*'+change.encode('ascii')+rb'\s+Done\s+.+\s+Install "certbot" snap\s*', line)
                for line in data.splitlines()))


def run(nonce, installed_change=None):
    global PHASE
    demand(re.fullmatch('[a-f0-9]{24}', nonce))
    host.preflight()
    # Neither an existing tool nor existing Certbot state is within this approval.
    paths = ('/snap/bin/certbot', '/etc/letsencrypt', '/var/lib/letsencrypt', '/var/log/letsencrypt',
             '/usr/bin/certbot', '/usr/local/bin/certbot')
    demand(Path('/usr/bin/snap').is_file() and Path('/usr/bin/openssl').is_file())
    if installed_change is None:
        demand(not any(os.path.lexists(p) for p in paths))
        mark('install_official_certbot')
        command('sudo','-n','/usr/bin/snap','install','--classic','certbot', timeout=120)
    else:
        demand(installed_change_done(installed_change, command('/usr/bin/snap','changes')))
        demand(os.access(CERTBOT,os.X_OK) and not any(os.path.lexists(p) for p in paths[1:]))
        mark('reuse_completed_install')
    installed = version(command(CERTBOT, '--version', timeout=15))
    print(json.dumps({'event':'certbot','version':installed}), flush=True)
    host.preflight()
    mark('request_staging_only')
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=f'travel-acme-test-{nonce}-', dir='/var/tmp') as scratch:
        work = Path(scratch)
        print(json.dumps({'event':'temporary_state','name':work.name}), flush=True)
        (work/'empty.ini').write_bytes(b'')
        for name in ('config','work','logs'):
            (work/name).mkdir(0o700)
        with prepared_webroot():
            command(*certbot_call(work), timeout=100)
            print(json.dumps({'event':'certbot_command_completed'}), flush=True)
        PHASE = 'inspect_staging_certificate'
        path = work/'config/live'/CERT/'cert.pem'
        demand(path.resolve(strict=True).is_relative_to(work/'config/archive'/CERT))
        result = command('/usr/bin/openssl','x509','-in',str(path),'-noout','-issuer','-ext','subjectAltName')
        demand(cert_is_exact(result))
        command('/usr/bin/openssl','x509','-in',str(path),'-noout','-checkend','3600')
        PHASE = 'verify_staging_renewal_metadata'
        renewal = (work/'config/renewal'/f'{CERT}.conf').read_text(encoding='utf-8')
        demand(re.findall(r'^server\s*=\s*(\S+)\s*$',renewal,re.M) == [STAGING])
    demand(not work.exists())
    PHASE = 'verify_unchanged_shared_configuration'
    host.preflight()
    return {'event':'verified','issuer_is_staging':True,'exact_ip_san':True,'temporary_state_removed':True,
            'shared_config_still_matches':True,'installed_certbot_retained':True,'public_tls_enabled':False,
            'elapsed_staging_seconds':round(time.monotonic()-start,2)}


if __name__ == '__main__':
    try:
        for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, host.interrupted)
        print(json.dumps(run(sys.argv[1], sys.argv[2] if len(sys.argv)==3 else None)), flush=True)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({'event':'failed','phase':PHASE,'error_type':type(error).__name__}), flush=True)
        raise SystemExit(1) from None
