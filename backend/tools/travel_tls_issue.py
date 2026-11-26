"""Review or submit the first authorized production certificate order on the travel host."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import acme_probe_remote as host
    import acme_staging_remote as acme
    import travel_tls_check as tls
    from travel_tls_edge import read_root_file, require
    from travel_tls_rotate import private_directory, sync_dir
else:
    from . import acme_probe_remote as host, acme_staging_remote as acme, travel_tls_check as tls
    from .travel_tls_edge import read_root_file, require
    from .travel_tls_rotate import private_directory, sync_dir


ROOT = Path('/etc/travel-tls')
WORK_ROOT = Path('/var/lib/travel-tls')
LOG_ROOT = Path('/var/log/travel-tls')
CERTBOT = '/snap/bin/certbot'


def certbot_order(contact):
    registration = (f'--email={contact}',) if contact else ('--register-unsafely-without-email',)
    return (
        CERTBOT, 'certonly', '--server', tls.PRODUCTION_CA, '--required-profile', 'shortlived',
        '--webroot', '--webroot-path', tls.WEBROOT, '--ip-address', tls.TARGET, '--cert-name', tls.CERT_NAME,
        '--non-interactive', '--agree-tos', *registration, '--no-directory-hooks',
        '--config', str(ROOT / 'empty.ini'), '--config-dir', str(tls.ACME),
        '--work-dir', str(WORK_ROOT / 'work'), '--logs-dir', str(LOG_ROOT),
    )


def check_shared_webroot():
    # Use the same checked permissions and active nginx config as the staging run.
    load = "import runpy, sys; runpy.run_path(sys.argv[1])['preflight']()"
    acme.command('/usr/sbin/runuser', '-u', 'ubuntu', '--', '/usr/bin/python3', '-I', '-B', '-c',
                 load, str(Path(host.__file__).resolve()), timeout=22)


def ensure_private_base(path):
    require(path.parent.resolve(strict=True) == path.parent, 'Unexpected base directory alias')
    path.mkdir(mode=0o700, exist_ok=True)
    private_directory(path)
    sync_dir(path.parent)


def record(stream, value):
    stream.write(json.dumps(value, sort_keys=True) + '\n')
    stream.flush()
    os.fsync(stream.fileno())


def issue(contact):
    check_shared_webroot()
    version = acme.version(acme.command(CERTBOT, '--version', timeout=15))
    require(tuple(map(int, version.split('.'))) >= (5, 10, 0), 'Reviewed Certbot webroot fix is required')
    os.umask(0o077)
    ensure_private_base(ROOT)
    ensure_private_base(WORK_ROOT)
    journal = ROOT / 'first-order.jsonl'
    empty = ROOT / 'empty.ini'
    work = WORK_ROOT / 'work'
    inputs = (tls.ACME, empty, work, LOG_ROOT)
    require(not any(os.path.lexists(path) for path in inputs), 'First-order state already exists')
    require(LOG_ROOT.parent.resolve(strict=True) == LOG_ROOT.parent, 'Unexpected log directory alias')

    # Exclusive reservation before creating certificate state prevents concurrent or
    # inadvertent repeated first orders after a lost connection.
    with journal.open('x', encoding='utf-8') as receipt:
        submitted = False
        record(receipt, {'event': 'reserved', 'certbot_version': version, 'ip': tls.TARGET})
        sync_dir(ROOT)
        try:
            for path in (tls.ACME, work, LOG_ROOT):
                path.mkdir(mode=0o700)
            with empty.open('xb'):
                pass
            with acme.prepared_webroot():
                record(receipt, {'event': 'invoking_production_certbot'})
                submitted = True
                acme.command(*certbot_order(contact), timeout=120)
            check_shared_webroot()
            tls.local_renewal_policy()
            lineage = tls.local_lineage(paired_key=True)
            require(lineage['status'] == 'ok' and lineage['key_match_checked'] is True,
                    'New trusted lineage is unverified or close to expiry')
        except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
            result = {'state': 'first_order_unverified', 'certbot_attempted': submitted,
                      'error_type': type(error).__name__, 'public_tls_verified': False}
        else:
            result = {'state': 'first_certificate_verified', 'certbot_attempted': True,
                      'fingerprint_sha256': lineage['fingerprint_sha256'], 'seconds_left': lineage['seconds_left'],
                      'challenge_cleanup_verified': True, 'shared_webroot_rechecked': True,
                      'public_tls_verified': False}
        record(receipt, result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--issue', action='store_true')
    registration = parser.add_mutually_exclusive_group()
    registration.add_argument('--use-contact', action='store_true')
    registration.add_argument('--no-contact', action='store_true')
    args = parser.parse_args()
    if not args.issue:
        print(json.dumps({'state': 'review_only', 'ip': tls.TARGET, 'profile': 'shortlived'}))
        return 0
    if not (args.use_contact or args.no_contact):
        parser.error('Select the approved account registration option')
    try:
        require(sys.platform == 'linux' and os.geteuid() == 0, 'Issue on the approved Linux host as root')
        for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, host.interrupted)
        contact = None
        if args.use_contact:
            contact = read_root_file(ROOT / 'acme-contact', 512).decode('ascii').strip()
            require(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', contact), 'Invalid ACME account contact')
        result = issue(contact)
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        result = {'state': 'first_order_refused', 'error_type': type(error).__name__, 'public_tls_verified': False}
    ok = result['state'] == 'first_certificate_verified'
    print(json.dumps(result), file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
