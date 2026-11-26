"""Review or run one authorized, disposable bridge rehearsal on the pinned host."""
import argparse
import json
from pathlib import Path
import secrets
import shlex
import subprocess

if __package__:
    from . import bind_travel_https_network as binder, run_acme_probe as probe
else:
    import bind_travel_https_network as binder
    import run_acme_probe as probe


CANDIDATE = binder.REVIEW / 'https-ip-entry-candidate'
EVIDENCE = binder.REPO / 'docs/verification/budget-https-entry-host-readback-2026-11-19.json'
RECEIPT = CANDIDATE / 'network-rehearsal-attempt.jsonl'
SOURCE = Path(__file__).with_name('rehearse_travel_https_network_remote.py')
SUCCESS = {
    'state': 'dedicated_bridge_verified', 'fixed_ip_after_recreation': True, 'maintenance_503': True,
    'private_gateway_forwarding': True, 'trusted_synthetic_tls': True,
    'no_published_ports': True, 'cleanup_verified': True,
}


def inputs():
    files, report = binder.candidate_files(EVIDENCE)
    if json.loads((CANDIDATE / 'report.json').read_bytes()) != report:
        raise ValueError('Candidate receipt changed')
    for name, value in files.items():
        if (CANDIDATE / name).read_bytes() != value.encode('utf-8'):
            raise ValueError('Candidate bytes changed')
    remote = SOURCE.read_bytes()
    helper = Path(__file__).with_name('travel_https_host.py').read_bytes()
    webroot = probe.reviewed_source()
    hashes = json.loads(EVIDENCE.read_bytes())['inputs_sha256']
    if probe.sha(helper) != hashes['backend/tools/travel_https_host.py'] or probe.sha(webroot) != hashes['backend/tools/acme_probe_remote.py']:
        raise ValueError('Remote inventories differ from the current approved review')
    compile(remote, SOURCE.name, 'exec')
    payload = json.dumps({'normal.conf': files['nginx-edge.conf'], 'maintenance.conf': files['nginx-edge-maintenance.conf'],
                          'host.py': helper.decode('utf-8'), 'webroot.py': webroot.decode('utf-8')}).encode('ascii')
    if len(payload) >= 32 * 1024 or len(remote) > 16 * 1024:
        raise ValueError('Unexpected rehearsal input size')
    return remote, payload


def execute(remote, payload, digest):
    token = secrets.token_hex(8)
    command = ('/usr/bin/timeout --signal=TERM --kill-after=24s 115s /usr/bin/python3 -I -B -c '
               + shlex.quote(remote.decode('utf-8')) + ' ' + token)
    with RECEIPT.open('x', encoding='utf-8') as receipt:
        receipt.write(json.dumps({'state': 'started', 'tag': token, 'bundle_sha256': digest}) + '\n')
        receipt.flush()
        try:
            result = subprocess.run([*probe.SSH, command], input=payload, capture_output=True, timeout=150)
            report = json.loads(result.stdout)
            accepted = result.returncode == 0 and report == SUCCESS
            outcome = {'report': report, 'ssh_exit': result.returncode, 'verified': accepted}
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            outcome = {'state': 'unconfirmed', 'error_type': type(error).__name__, 'verified': False}
        receipt.write(json.dumps(outcome) + '\n')
    print(json.dumps(outcome))
    return 0 if outcome['verified'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--expected-sha256')
    args = parser.parse_args()
    try:
        remote, payload = inputs()
        digest = probe.sha(remote + b'\0' + payload)
        if args.execute:
            if args.expected_sha256 != digest:
                parser.error('Exact approved rehearsal bundle digest required')
            return execute(remote, payload, digest)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f'Rehearsal refused: {error}\n')
    print(json.dumps({'state': 'review_only', 'bundle_sha256': digest, 'bytes': len(remote) + len(payload),
                      'attempt_exists': RECEIPT.exists(), 'server_contacted': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
