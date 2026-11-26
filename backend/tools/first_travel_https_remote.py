"""Submit one first certificate order and verify it on an unpublished maintenance edge."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile


MODULES = {
    'travel_tls_issue.py', 'travel_tls_check.py', 'travel_tls_edge.py', 'travel_tls_rotate.py',
    'acme_probe_remote.py', 'acme_staging_remote.py', 'travel_https_host.py',
    'rehearse_travel_https_network_remote.py',
}
COMMAND = ('/usr/bin/docker',)
CONFIG = 'e2ba23f487a2ede868983373fc3af0d60652c7b6ab4b1da4bc2d945240288e1e'
PHASE = 'preflight'
RESULT = {'public_443_published': False}


def demand(value):
    if not value:
        raise ValueError('Unverified first HTTPS activation')


def command(*args):
    return subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=10).stdout


def write_once(path, data, cleanup):
    with path.open('xb') as output:
        cleanup.callback(path.unlink)
        output.write(data)


def verify_unpublished(tag, image, conf, edge, rehearsal):
    global PHASE
    edge.STORE.mkdir(mode=0o700)
    store = edge.rotate.Store(edge.STORE)
    edge.bootstrap(store)
    digest = store.current()
    demand(digest == RESULT['certificate']['fingerprint_sha256'])
    PHASE = 'maintenance'
    capabilities = ('NET_BIND_SERVICE', 'SETUID', 'SETGID', 'CHOWN', 'KILL')
    with ExitStack() as cleanup:
        write_once(edge.CONF, conf, cleanup)
        try:
            container = command(
                *COMMAND, 'run', '--pull', 'never', '--detach', '--rm', '--name', edge.NAME.lstrip('/'),
                '--label', f'{rehearsal.LABEL}={tag}', '--label', f'travel.edge.role={edge.ROLE}',
                '--network', rehearsal.NETWORK, '--ip', rehearsal.PROXY,
                '--user', '0:0', '--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=24m,mode=1777',
                '--memory', '160m', '--memory-swap', '160m', '--cpus', '0.25', '--pids-limit', '32',
                '--cap-drop', 'ALL', *(item for cap in capabilities for item in ('--cap-add', cap)),
                '--security-opt', 'no-new-privileges',
                '--mount', f'type=bind,source={edge.CONF},target={edge.TARGET_CONF},readonly',
                '--mount', f'type=bind,source={edge.STORE},target={edge.TARGET_STORE},readonly',
                '--entrypoint', 'nginx', image, '-c', edge.TARGET_CONF, '-g', 'daemon off;',
            ).decode('ascii').strip()
            demand(rehearsal.owned('container', tag) == [container])
            metadata = {'id': container, 'image': image, 'network': rehearsal.NETWORK,
                        'ip': rehearsal.PROXY, 'config_sha256': CONFIG}
            write_once(edge.PIN, json.dumps(metadata).encode('ascii'), cleanup)
            target = edge.Edge(edge.parse_target(edge.read_root_file(edge.PIN, 4096)), unpublished=True)
            verified = edge.verify_first(store, target)
            demand(verified['state'] == 'first_maintenance_leaf_verified')
            RESULT['unpublished_maintenance_verified'] = True
        finally:
            for container in rehearsal.owned('container', tag):
                command(*COMMAND, 'rm', '-f', container)
            demand(not rehearsal.owned('container', tag))
            RESULT['container_removed'] = True


def run(tag, registration, payload):
    global PHASE
    demand(os.geteuid() == 0 and sys.flags.isolated and re.fullmatch('[a-f0-9]{16}', tag))
    demand(registration in ('no-contact', 'use-contact'))
    demand(set(payload) == MODULES | {'maintenance.conf'})
    os.umask(0o077)
    conf = payload['maintenance.conf'].encode('utf-8')
    demand(hashlib.sha256(conf).hexdigest() == CONFIG)
    with tempfile.TemporaryDirectory(prefix='travel-first-https-'+tag+'-', dir='/run') as temporary:
        directory = Path(temporary)
        for name in MODULES:
            (directory/name).write_text(payload[name], encoding='utf-8')
        # The preflight reads only this root-owned source when run as ubuntu.
        directory.chmod(0o711)
        (directory/'acme_probe_remote.py').chmod(0o644)
        sys.path.insert(0, temporary)
        import rehearse_travel_https_network_remote as rehearsal
        import travel_https_host as host
        import travel_tls_edge as edge
        import travel_tls_issue as issuance

        rehearsal.DOCKER = COMMAND
        issuance.check_shared_webroot()
        candidate = host.network_candidate(command, COMMAND)
        demand(not any(candidate[key] for key in ('overlaps_observed_routes', 'overlaps_observed_docker_ipam',
                                                  'interface_name_in_use', 'docker_name_in_use')))
        demand((candidate['subnet'], candidate['gateway'], candidate['proxy_ip']) ==
               (rehearsal.SUBNET, rehearsal.GATEWAY, rehearsal.PROXY))
        demand(not host.listeners({'show': command}, 443))
        demand(not host.publishes_443(command(*COMMAND, 'ps', '--format', '{{.Ports}}')))
        demand(edge.NAME.lstrip('/') not in command(*COMMAND, 'ps', '-a', '--format', '{{.Names}}').decode('ascii').splitlines())
        demand(not any(os.path.lexists(path) for path in (edge.CONF, edge.PIN, edge.STORE)))
        image = command(*COMMAND, 'inspect', '--format', '{{.Image}}', 'continew-web').decode('ascii').strip()
        demand(re.fullmatch('sha256:[a-f0-9]{64}', image))
        demand(command(*COMMAND, 'image', 'inspect', '--format', '{{json .Config.Volumes}}', image).strip() in (b'null', b'{}'))

        PHASE = 'create_bridge'
        try:
            with rehearsal.bridge(tag):
                PHASE = 'production_order'
                proc = subprocess.run(
                    ('/usr/bin/python3', '-I', '-B', str(directory/'travel_tls_issue.py'), '--issue', '--'+registration),
                    stdin=subprocess.DEVNULL, capture_output=True, timeout=165,
                )
                channel = proc.stdout if proc.returncode == 0 else proc.stderr
                RESULT['certificate'] = json.loads(channel.splitlines()[-1])
                demand(proc.returncode == 0 and RESULT['certificate']['state'] == 'first_certificate_verified')
                PHASE = 'certificate_store'
                verify_unpublished(tag, image, conf, edge, rehearsal)
        finally:
            RESULT['bridge_removed'] = not rehearsal.owned('network', tag)
        PHASE = 'postflight'
        demand(RESULT['bridge_removed'] and host.network_candidate(command, COMMAND) == candidate)
        demand(not any(os.path.lexists(path) for path in (edge.CONF, edge.PIN)))
        demand(not host.listeners({'show': command}, 443))
        demand(not host.publishes_443(command(*COMMAND, 'ps', '--format', '{{.Ports}}')))
        issuance.check_shared_webroot()
    demand(not directory.exists())
    return {**RESULT, 'state': 'production_certificate_and_private_maintenance_verified',
            'workspace_removed': True, 'shared_webroot_rechecked': True}


def interrupted(*args):
    raise RuntimeError('First HTTPS verification interrupted')


if __name__ == '__main__':
    for sig in (signal.SIGINT, signal.SIGHUP, signal.SIGTERM):
        signal.signal(sig, interrupted)
    try:
        print(json.dumps(run(sys.argv[1], sys.argv[2], json.loads(sys.stdin.buffer.read(96 * 1024)))))
    except (ValueError, RuntimeError, IndexError, KeyError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({**RESULT, 'state': 'not_verified', 'phase': PHASE, 'error_type': type(error).__name__}))
        raise SystemExit(1) from None
