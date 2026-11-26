"""Review or execute the authorized one-time public certificate and unpublished TLS check."""
import argparse
import json
from pathlib import Path
import secrets
import shlex
import subprocess

if __package__:
    from . import run_acme_probe as probe
    from .first_travel_https_remote import CONFIG, MODULES
else:
    import run_acme_probe as probe
    from first_travel_https_remote import CONFIG, MODULES


CANDIDATE = probe.REPO / '.release-candidates/budget-https-load-2026-11-19/https-ip-entry-candidate'
DOCS = probe.REPO / 'docs/verification'
TOOLS = Path(__file__).resolve().parent
ATTEMPT = CANDIDATE / 'first-production-https-attempt.jsonl'


def inputs():
    issuance = json.loads((DOCS/'budget-ip-first-production-order-candidate-2026-11-19.json').read_bytes())
    network = json.loads((DOCS/'budget-https-entry-network-rehearsal-2026-11-19.json').read_bytes())
    executed = (probe.REPO/network['execution_receipt']).read_bytes()
    if (network['host'] != probe.TARGET or network['verified'] is not True
            or network['checks']['cleanup_verified'] is not True or probe.sha(executed) != network['execution_receipt_sha256']):
        raise ValueError('Successful bridge rehearsal and cleanup evidence required')
    checksums = {Path(name).name: value for name, value in issuance['runtime_source_sha256'].items()}
    checksums['travel_https_host.py'] = json.loads(
        (DOCS/'budget-https-entry-host-readback-2026-11-19.json').read_bytes())['inputs_sha256']['backend/tools/travel_https_host.py']
    checksums['rehearse_travel_https_network_remote.py'] = json.loads(
        (DOCS/'budget-https-entry-network-candidate-2026-11-19.json').read_bytes())['source_sha256'][
            'backend/tools/rehearse_travel_https_network_remote.py']
    if set(checksums) != MODULES:
        raise ValueError('Unexpected certificate bundle files')
    payload = {}
    for name, digest in checksums.items():
        data = (TOOLS/name).read_bytes()
        if probe.sha(data) != digest:
            raise ValueError(f'Changed source: {name}')
        payload[name] = data.decode('utf-8')
    maintenance = (CANDIDATE/'nginx-edge-maintenance.conf').read_bytes()
    if probe.sha(maintenance) != CONFIG:
        raise ValueError('Changed maintenance configuration')
    payload['maintenance.conf'] = maintenance.decode('utf-8')
    source = (TOOLS/'first_travel_https_remote.py').read_bytes()
    compile(source, 'first_travel_https_remote.py', 'exec')
    package = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode('ascii')
    if len(package) >= 96 * 1024 or len(source) > 16 * 1024:
        raise ValueError('Unexpected HTTPS payload size')
    return source, package


def execute(source, package, digest):
    tag = secrets.token_hex(8)
    remote = ('/usr/bin/sudo -n /usr/bin/timeout --signal=TERM --kill-after=24s 230s /usr/bin/python3 -I -B -c '
              + shlex.quote(source.decode('utf-8')) + ' ' + tag + ' no-contact')
    with ATTEMPT.open('x', encoding='utf-8') as receipt:
        receipt.write(json.dumps({'state': 'started', 'tag': tag, 'bundle_sha256': digest, 'account_contact': False}) + '\n')
        receipt.flush()
        try:
            process = subprocess.run([*probe.SSH, remote], input=package, capture_output=True, timeout=265)
            report = json.loads(process.stdout)
            verified = process.returncode == 0 and report['state'] == 'production_certificate_and_private_maintenance_verified'
            outcome = {'report': report, 'ssh_exit': process.returncode, 'verified': verified}
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            outcome = {'state': 'unconfirmed', 'error_type': type(error).__name__, 'verified': False}
        receipt.write(json.dumps(outcome) + '\n')
    print(json.dumps(outcome))
    return 0 if outcome['verified'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--issue-verify-no-contact', action='store_true')
    parser.add_argument('--expected-sha256')
    args = parser.parse_args()
    try:
        source, package = inputs()
        digest = probe.sha(source + b'\0' + package)
        if args.issue_verify_no_contact:
            if args.expected_sha256 != digest:
                parser.error('The exact authorized certificate bundle digest is required')
            return execute(source, package, digest)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f'First HTTPS operation refused: {error}\n')
    print(json.dumps({'state': 'review_only', 'host': probe.TARGET, 'bundle_sha256': digest,
                      'bytes': len(source)+len(package), 'attempt_exists': ATTEMPT.exists()}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
