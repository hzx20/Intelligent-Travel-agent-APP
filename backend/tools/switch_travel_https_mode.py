"""Replace the dedicated travel HTTPS proxy after the covered cutover checks."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys

if __package__:
    from . import guard_travel_write_opening as opening, register_travel_tls_edge as registration
    from . import travel_tls_edge as edge, verify_travel_cutover_ready as readiness
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import guard_travel_write_opening as opening
    import register_travel_tls_edge as registration
    import travel_tls_edge as edge
    import verify_travel_cutover_ready as readiness


LABEL = 'travel.edge.activation'
MODES = registration.EDGE_CONFIGS
CAPABILITIES = ('NET_BIND_SERVICE', 'SETUID', 'SETGID', 'CHOWN', 'KILL')
FAVICON_FRONTEND = {
    'frontend/index.html': '1f5a97b6125f11467d10d3edef55484c134eaee0f89b91708a158a5dbc7fe5e0',
    'frontend/.vite/manifest.json': 'ccbfb675a4462289e4e4f7c9330bc10e1acae08b7ca0162a21e5b6bcd1c185a7',
    'frontend/assets/travel-favicon-Clb1qSVb.png': '5fe452becfd5a6a5b40be49a78a43900431ca5804f24f99d5408c968c7d065c4',
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_candidate(path, mode):
    edge.require(path.is_absolute() and path.resolve(strict=True) == path and path.is_file(),
                 'Expected a canonical reviewed configuration')
    with path.open('rb') as stream:
        data = stream.read(65537)
    edge.require(sha(data) == MODES[mode], 'Reviewed proxy configuration changed')
    return data


def docker_arguments(spec, mode, tag, *, syntax_config=None):
    cap_flags = [part for capability in CAPABILITIES for part in ('--cap-add', capability)]
    restrictions = ['--pull', 'never', '--user', '0:0', '--read-only',
                    '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=24m,mode=1777',
                    '--memory', '160m', '--memory-swap', '160m', '--cpus', '0.25', '--pids-limit', '32',
                    '--cap-drop', 'ALL', *cap_flags, '--security-opt', 'no-new-privileges']
    if syntax_config is None:
        placement = ['--detach', '--name', edge.NAME.lstrip('/'), '--label', f'travel.edge.role={edge.ROLE}',
                     '--restart', 'unless-stopped', '--network', spec['network'], '--ip', spec['ip'],
                     '--publish', '0.0.0.0:443:443/tcp']
        nginx_args = ['-c', edge.TARGET_CONF, '-g', 'daemon off;']
    else:
        placement = ['--rm', '--network', 'none']
        nginx_args = ['-t', '-c', edge.TARGET_CONF]
    config = edge.CONF if syntax_config is None else syntax_config
    mounts = ['--mount', f'type=bind,source={config},target={edge.TARGET_CONF},readonly',
              '--mount', f'type=bind,source={edge.STORE},target={edge.TARGET_STORE},readonly']
    if mode == 'normal':
        mounts += ['--mount', f'type=bind,source={edge.STATIC},target={edge.TARGET_STATIC},readonly']
    return ['run', '--label', f'{LABEL}={tag}', *restrictions, *placement, *mounts,
            '--entrypoint', 'nginx', spec['image'], *nginx_args]


def containers(*filters):
    out = edge.docker('ps', '-a', '--no-trunc', '-q', *(part for rule in filters for part in ('--filter', rule)))
    found = out.decode('ascii').splitlines()
    edge.require(len(found) <= 1 and all(re.fullmatch('[a-f0-9]{64}', value) for value in found),
                 'Unexpected dedicated edge container inventory')
    return found


def published(spec, mode, tag):
    result = edge.docker(*docker_arguments(spec, mode, tag)).decode('ascii').strip()
    edge.require(containers(f'label={LABEL}={tag}') == [result], 'Exact new container could not be identified')
    return {**spec, 'id': result, 'config_sha256': MODES[mode]}


def verify_static():
    source = Path(readiness.WORK) / 'release.json'
    manifest = edge.read_root_file(source, 65536, public_certificate=True)
    edge.require(sha(manifest) == readiness.PINNED[str(source)], 'Frozen frontend manifest changed')
    payload = json.loads(manifest)['files']
    target = Path(edge.STATIC)
    edge.require(target.is_dir() and target.resolve(strict=True) == target, 'Published frontend path differs')
    entry = target / 'index.html'
    edge.require(entry.resolve(strict=True) == entry and entry.is_file() and entry.stat().st_size <= 1024 * 1024,
                 'Published frontend entry differs')
    if sha(entry.read_bytes()) == FAVICON_FRONTEND['frontend/index.html']:
        payload.update(FAVICON_FRONTEND)
    for name, checksum in payload.items():
        if name.startswith('frontend/') and name != 'frontend/release.json':
            path = target / name.removeprefix('frontend/')
            edge.require(path.resolve(strict=True) == path and path.is_file() and path.stat().st_size <= 1024 * 1024,
                         'Published frontend contains an unexpected file')
            edge.require(sha(path.read_bytes()) == checksum, 'Published frontend differs from the frozen release')


def confirm_and_store(spec, store, known_pin, approved_pins):
    target = edge.Edge(spec)
    edge.require(edge.rotate.wait_for_leaf(target.peer, store.current()), 'Replacement did not load the selected leaf')
    fingerprint = registration.verify_certificate(store, spec)
    control = subprocess.run(('/usr/bin/curl', '-q', '--noproxy', '*', '--silent', '--show-error', '--max-time', '4',
                              '--connect-to', f'{edge.tls.TARGET}:443:127.0.0.1:443', '--output', '/dev/null',
                              '--write-out', '%{http_code}', f'https://{edge.tls.TARGET}/travel/api/health'),
                             check=True, stdin=subprocess.DEVNULL, capture_output=True, timeout=5).stdout
    expected = b'503' if spec['config_sha256'] == MODES['maintenance'] else b'200'
    edge.require(control == expected, 'Replacement API did not match the selected mode')
    registration.require_quiet_renewal()
    current_pin = edge.read_root_file(edge.PIN, 4096)
    edge.require(current_pin in approved_pins, 'Renewal target changed outside the operation')
    candidate = (json.dumps(spec, sort_keys=True) + '\n').encode('ascii')
    approved_pins.add(candidate)
    if current_pin != candidate:
        registration.replace_target(edge.PIN, current_pin, candidate)
    edge.require(edge.read_root_file(edge.PIN, 4096) == candidate, 'Renewal target readback differs')
    return {'container_id': spec['id'], 'previous_target_sha256': sha(known_pin),
            'target_sha256': sha(candidate), 'public_leaf_sha256': fingerprint}


def replace_running(mode, config, maintenance, opening_record, snapshot_digest, expected_pin):
    registration.require_quiet_renewal()
    edge.rotate.private_directory(edge.ROOT)
    edge.rotate.private_directory(edge.STORE / 'generations')
    store = edge.rotate.Store(edge.STORE)
    with store.locked():
        raw_pin = edge.read_root_file(edge.PIN, 4096)
        spec = edge.parse_target(raw_pin)
        old_mode = 'maintenance' if mode == 'normal' else 'normal'
        edge.require(sha(raw_pin) == expected_pin and spec['config_sha256'] == MODES[old_mode]
                     and spec['network'] == 'travel-edge' and spec['ip'] == readiness.PROXY,
                     'Current edge target differs from the review')
        old_config = edge.read_root_file(edge.CONF, 65536)
        edge.require(sha(old_config) == MODES[old_mode], 'Current edge configuration differs')
        edge.require(edge.Edge(spec).guarded() == spec['id'], 'Current proxy could not be confirmed')
        if mode == 'normal':
            readiness.verify_while_closed()
            verify_static()
            opening.check_directory(opening_record)
            edge.require(not os.path.lexists(opening_record), 'Opening record already exists')
        # The temporary syntax container has no network or published port.
        syntax_tag = secrets.token_hex(8)
        try:
            edge.docker(*docker_arguments(spec, mode, syntax_tag, syntax_config=config))
        finally:
            for attempt in containers(f'label={LABEL}={syntax_tag}'):
                edge.docker('rm', '-f', attempt)

        new_tag = secrets.token_hex(8)
        approved_pins = {raw_pin}
        proposed = read_candidate(config, mode)
        previous = read_candidate(maintenance, 'maintenance')
        try:
            registration.require_quiet_renewal()
            edge.docker('rm', '-f', spec['id'])
            registration.replace_target(edge.CONF, old_config, proposed)
            if mode == 'normal':
                opening.mark_opening(opening_record, readiness.APP, snapshot_digest)
            target = published(spec, mode, new_tag)
            result = confirm_and_store(target, store, raw_pin, approved_pins)
            return {**result, 'state':f'{mode}_edge_verified', 'renewal_timer_still_stopped': True}
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
            try:
                for attempt in containers(f'label={LABEL}={new_tag}'):
                    edge.docker('rm', '-f', attempt)
                existing = containers(f'name=^/{edge.NAME.lstrip("/")}$')
                if existing:
                    edge.require(mode == 'normal' and existing == [spec['id']], 'Unexpected edge during recovery')
                    target = spec
                else:
                    current = edge.read_root_file(edge.CONF, 65536)
                    edge.require(current in (old_config, proposed), 'Edge configuration changed outside the operation')
                    if current != previous:
                        registration.replace_target(edge.CONF, current, previous)
                    target = published(spec, 'maintenance', secrets.token_hex(8))
                result = confirm_and_store(target, store, raw_pin, approved_pins)
                return {**result, 'state':'maintenance_restored', 'error_type':type(error).__name__,
                        'renewal_timer_still_stopped':True}
            except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as recovery:
                return {'state':'maintenance_recovery_unconfirmed', 'error_type':type(error).__name__,
                        'recovery_error_type':type(recovery).__name__}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=MODES, required=True)
    parser.add_argument('--configuration', type=Path, required=True)
    parser.add_argument('--maintenance-configuration', type=Path, required=True)
    parser.add_argument('--previous-target-sha256', required=True)
    parser.add_argument('--backup-sha256')
    parser.add_argument('--opening-record', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    values = [args.previous_target_sha256]
    if args.mode == 'normal':
        values.append(args.backup_sha256 or '')
        if args.opening_record is None:
            parser.error('Normal mode requires a private opening record')
    if not all(re.fullmatch('[a-f0-9]{64}', value) for value in values):
        parser.error('Use exact reviewed SHA-256 values')
    try:
        config, maintenance = args.configuration.absolute(), args.maintenance_configuration.absolute()
        read_candidate(config, args.mode)
        read_candidate(maintenance, 'maintenance')
        if not args.apply:
            print(json.dumps({'state':'review_only','mode':args.mode,'config_sha256':MODES[args.mode]}))
            return 0
        edge.require(sys.platform == 'linux' and os.geteuid() == 0, 'Expected the approved Linux origin')

        def interrupted(*_):
            raise InterruptedError('Proxy replacement interrupted')

        for name in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            signal.signal(name, interrupted)
        record = args.opening_record.absolute() if args.opening_record else None
        report = replace_running(args.mode, config, maintenance, record, args.backup_sha256, args.previous_target_sha256)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        report = {'state':'precondition_unconfirmed','error_type':type(error).__name__}
    print(json.dumps(report))
    return int(report['state'] != f'{args.mode}_edge_verified')


if __name__ == '__main__':
    raise SystemExit(main())
