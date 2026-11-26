"""Register the exact rebuilt travel HTTPS proxy for automatic certificate loading."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

if __package__:
    from . import travel_tls_edge as edge
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import travel_tls_edge as edge


EDGE_CONFIGS = {
    'maintenance': 'e2ba23f487a2ede868983373fc3af0d60652c7b6ab4b1da4bc2d945240288e1e',
    'normal': edge.DEDICATED_NORMAL,
}
RENEWAL = 'travel-ip-renew.service'
TIMER = 'travel-ip-renew.timer'


def require_quiet_renewal():
    output = subprocess.run(('/usr/bin/systemctl', 'show', '--no-pager', '-p', 'ActiveState,Job,Id', TIMER, RENEWAL),
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True, timeout=6).stdout
    units = [dict(line.split('=', 1) for line in block.splitlines()) for block in output.strip().split('\n\n')]
    allowed = {TIMER: {'inactive'}, RENEWAL: {'inactive', 'failed'}}
    edge.require({unit.get('Id') for unit in units} == set(allowed) and all(
        unit.get('ActiveState') in allowed[unit['Id']] and unit.get('Job') == '' for unit in units
    ), 'Stop the renewal timer and let its service finish before registering an edge')


def checked_target(raw, previous_sha256, mode, container_id):
    edge.require(hashlib.sha256(raw).hexdigest() == previous_sha256, 'Registered edge changed since review')
    previous = edge.parse_target(raw)
    edge.require(previous['network'] == 'travel-edge' and previous['ip'] == '172.31.240.2',
                 'The reviewed dedicated edge network is required')
    edge.require(previous['config_sha256'] in EDGE_CONFIGS.values(), 'Expected a dedicated edge configuration')
    proposed = {**previous, 'id': container_id, 'config_sha256': EDGE_CONFIGS[mode]}
    return edge.parse_target(json.dumps(proposed))


def verify_certificate(store, proposed):
    current = store.current()
    saved = edge.tls.checked_lineage_path(edge.tls.ACME, 'cert.pem')
    edge.require(edge.rotate.fingerprint(saved.read_bytes()) == current, 'Saved and selected certificate differ')
    proxy = edge.Edge(proposed)
    proxy.check()
    edge.require(proxy.peer() == current, 'Running proxy did not serve the selected leaf')
    served = edge.tls.public_probe(expected_pem=saved)
    edge.require(served['matches_expected_leaf'] is True and served['fingerprint_sha256'] == current
                 and served['status'] in ('ok', 'warning', 'critical'), 'Public HTTPS did not serve the selected leaf')
    return current


def replace_target(path, old, raw):
    fd, scratch = tempfile.mkstemp(prefix='.travel-edge-target-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as file:
            os.fchmod(file.fileno(), 0o600)
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
        edge.require(edge.read_root_file(path, 4096) == old, 'Registered edge changed during verification')
        os.replace(scratch, path)
        edge.rotate.sync_dir(path.parent)
    finally:
        if os.path.lexists(scratch):
            Path(scratch).unlink()


def register(mode, container_id, previous_sha256):
    require_quiet_renewal()
    edge.rotate.private_directory(edge.ROOT)
    # The store belongs to an already published edge and must already exist.
    edge.rotate.private_directory(edge.STORE / 'generations')
    store = edge.rotate.Store(edge.STORE)
    with store.locked():
        old = edge.read_root_file(edge.PIN, 4096)
        proposed = checked_target(old, previous_sha256, mode, container_id)
        fingerprint = verify_certificate(store, proposed)
        require_quiet_renewal()
        raw = (json.dumps(proposed, sort_keys=True) + '\n').encode('ascii')
        replace_target(edge.PIN, old, raw)
        edge.require(edge.read_root_file(edge.PIN, 4096) == raw
                     and stat.S_IMODE(edge.PIN.stat().st_mode) == 0o600, 'Readback of the target failed')
        require_quiet_renewal()
    return {'state': 'target_registered', 'mode': mode, 'target_sha256': hashlib.sha256(raw).hexdigest(),
            'public_leaf_sha256': fingerprint, 'renewal_timer_still_stopped': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=EDGE_CONFIGS, required=True)
    parser.add_argument('--container-id', required=True)
    parser.add_argument('--previous-target-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    for value in (args.container_id, args.previous_target_sha256):
        if not re.fullmatch('[a-f0-9]{64}', value):
            parser.error('Use full lowercase SHA-256 identifiers')
    if not args.apply:
        print(json.dumps({'state': 'review_only', 'mode': args.mode, 'configuration_sha256': EDGE_CONFIGS[args.mode]}))
        return 0
    try:
        edge.require(sys.platform == 'linux' and os.geteuid() == 0, 'Expected root on the approved Linux host')
        result = register(args.mode, args.container_id, args.previous_target_sha256)
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps({'state': 'registration_unconfirmed', 'error_type': type(error).__name__}), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
