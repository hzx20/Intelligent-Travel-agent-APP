"""Exercise Certbot renewal using an isolated temporary staging lineage."""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time


NAME = 'travel-ip-renewal-fixture'
STAGING = 'https://acme-staging-v02.api.letsencrypt.org/directory'
CERTBOT = '/snap/bin/certbot'
TARGET = '134.175.137.18'
WEBROOT = '/opt/continew/web'
PHASE = 'preflight'


def require(condition):
    if not condition:
        raise ValueError('Staging renewal check failed')


def options(work):
    return ('--server', STAGING, '--cert-name', NAME, '--required-profile', 'shortlived',
            '--non-interactive', '--no-directory-hooks',
            '--config', str(work/'empty.ini'), '--config-dir', str(work/'config'),
            '--work-dir', str(work/'work'), '--logs-dir', str(work/'logs'))


def commands(work):
    common = options(work)
    issue = (CERTBOT, 'certonly', *common, '--webroot', '--webroot-path', WEBROOT,
             '--ip-address', TARGET, '--agree-tos', '--register-unsafely-without-email')
    renew = (CERTBOT, 'renew', *common, '--dry-run', '--disable-renew-updates', '--no-random-sleep-on-renew')
    return issue, renew


def confirmed_renewal(output, chain):
    lines = [line.strip() for line in output.splitlines()]
    successes = [line for line in lines if line.endswith(b' (success)')]
    return successes == [os.fsencode(chain)+b' (success)']


def lineage_snapshot(work):
    live = work/'config/live'/NAME
    archive = work/'config/archive'/NAME
    hashes = {}
    for leaf in ('cert.pem','chain.pem','fullchain.pem','privkey.pem'):
        target = (live/leaf).resolve(strict=True)
        require(target.parent == archive and target.name == leaf.removesuffix('.pem')+'1.pem')
        hashes[leaf] = hashlib.sha256(target.read_bytes()).digest()
    return hashes


def run(payload):
    global PHASE
    require(os.geteuid() != 0 and sys.flags.isolated and set(payload) == {'acme_probe_remote.py','acme_staging_remote.py'})
    os.umask(0o077)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='travel-renewal-staging-', dir='/var/tmp') as scratch:
        work = Path(scratch)
        for name, content in payload.items():
            (work/name).write_text(content, encoding='utf-8')
        sys.path.insert(0, scratch)
        import acme_staging_remote as acme

        acme.host.preflight()
        version = acme.version(acme.command(CERTBOT,'--version',timeout=12))
        require(tuple(map(int,version.split('.'))) >= (5,10,0))
        for path in ('config','work','logs'):
            (work/path).mkdir(0o700)
        (work/'empty.ini').write_bytes(b'')
        issue, renew = commands(work)
        PHASE = 'staging_first_certificate'
        with acme.prepared_webroot():
            acme.command(*issue, timeout=65)
        before = lineage_snapshot(work)
        live = work/'config/live'/NAME
        details = acme.command('/usr/bin/openssl','x509','-in',str(live/'cert.pem'),'-noout','-issuer','-ext','subjectAltName')
        require(acme.cert_is_exact(details))
        conf = (work/'config/renewal'/f'{NAME}.conf').read_text(encoding='utf-8')
        require(re.findall(r'^server\s*=\s*(\S+)\s*$',conf,re.M)==[STAGING])
        require(re.findall(r'^authenticator\s*=\s*(\S+)\s*$',conf,re.M)==['webroot'])
        require(not re.search(r'^\s*(?:pre_hook|post_hook|deploy_hook|renew_hook)\s*=',conf,re.M))
        PHASE = 'staging_dry_run_renewal'
        with acme.prepared_webroot():
            result = acme.command(*renew, timeout=65)
        require(confirmed_renewal(result,live/'fullchain.pem'))
        require(lineage_snapshot(work)==before)
        acme.host.preflight()
    require(not work.exists())
    return {'event':'verified','staging_only':True,'test_ip_exact':True,'dry_run_success_report_seen':True,
            'dry_run_retained_original_test_lineage':True,'temporary_state_removed':True,
            'shared_webroot_rechecked':True,'certbot_version':version,'elapsed_seconds':round(time.monotonic()-started,2)}


def interrupted(*_):
    raise RuntimeError('Staging renewal interrupted')


if __name__=='__main__':
    for sig in (signal.SIGHUP,signal.SIGTERM,signal.SIGINT):
        signal.signal(sig,interrupted)
    try:
        print(json.dumps(run(json.loads(sys.stdin.buffer.read(20*1024)))),flush=True)
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
        print(json.dumps({'event':'failed','phase':PHASE,'error_type':type(error).__name__}),flush=True)
        raise SystemExit(1) from None
