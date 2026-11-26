"""Hold the dedicated public maintenance edge only after the caller verifies it."""
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import tempfile


MODULES = {'travel_tls_check.py', 'travel_tls_rotate.py', 'travel_tls_edge.py', 'travel_https_host.py', 'acme_probe_remote.py'}
DOCKER = '/usr/bin/docker'
LABEL = 'travel.edge.activation'
MAINTENANCE = 'e2ba23f487a2ede868983373fc3af0d60652c7b6ab4b1da4bc2d945240288e1e'
PHASE = 'preflight'


def require(ok):
    if not ok:
        raise ValueError('Maintenance edge check failed')


def call(*cmd):
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=8).stdout


def owned(tag, *, network=False):
    verb = ('network', 'ls') if network else ('ps', '-a')
    ids = call(DOCKER, *verb, '--no-trunc', '--filter', f'label={LABEL}={tag}', '-q').decode('ascii').splitlines()
    require(len(ids) <= 1 and all(re.fullmatch('[a-f0-9]{64}', value) for value in ids))
    return ids


def select_saved_certificate(store, edge):
    previous = store.current()
    require(edge.tls.local_renewal_policy()['status'] == 'ok')
    lineage = edge.tls.local_lineage(paired_key=True)
    require(lineage['status'] == 'ok')
    selected = store.stage(*edge.saved_pair(), edge.validate_pair)
    require(selected == lineage['fingerprint_sha256'])
    return previous, selected


def publish_one(tag, config, helper_path):
    global PHASE
    import travel_https_host as host
    import travel_tls_edge as edge

    def check_http():
        load = "import runpy, sys; runpy.run_path(sys.argv[1])['preflight']()"
        subprocess.run(('/usr/sbin/runuser', '-u', 'ubuntu', '--', '/usr/bin/python3', '-I', '-B', '-c',
                        load, str(helper_path)), stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=22)

    check_http()
    plan = host.network_candidate(call, (DOCKER,))
    require(not any(plan[key] for key in ('overlaps_observed_routes', 'overlaps_observed_docker_ipam',
                                          'docker_name_in_use', 'interface_name_in_use')))
    require(not host.listeners({'show': call}, 443))
    require(not host.publishes_443(call(DOCKER, 'ps', '--format', '{{.Ports}}')))
    require(edge.NAME.lstrip('/') not in call(DOCKER, 'ps', '-a', '--format', '{{.Names}}').decode('ascii').splitlines())
    require(not any(os.path.lexists(path) for path in (edge.CONF, edge.PIN)))
    image = call(DOCKER, 'inspect', '--format', '{{.Image}}', 'continew-web').decode('ascii').strip()
    require(re.fullmatch('sha256:[a-f0-9]{64}', image))
    require(call(DOCKER,'image','inspect','--format','{{json .Config.Volumes}}',image).strip() in (b'null',b'{}'))
    edge.rotate.private_directory(edge.ROOT)
    edge.rotate.private_directory(edge.STORE/'generations')
    store = edge.rotate.Store(edge.STORE)
    with store.locked():
        previous, selected = select_saved_certificate(store, edge)
        return publish_edge(tag, config, plan, image, check_http, edge, store, previous, selected)


def publish_edge(tag, config, plan, image, check_http, edge, store, previous, selected):
    global PHASE
    capabilities = ('NET_BIND_SERVICE', 'SETUID', 'SETGID', 'CHOWN', 'KILL')
    retained = False
    staged = []
    try:
        PHASE = 'saved_certificate'
        if selected != previous:
            store.point(selected)
        PHASE = 'network'
        network = call(DOCKER, 'network', 'create', '--driver', 'bridge',
                       '--opt', f'com.docker.network.bridge.name={plan["host_interface"]}',
                       '--subnet', plan['subnet'], '--gateway', plan['gateway'], '--ip-range', plan['dynamic_range'],
                       '--label', f'{LABEL}={tag}', plan['docker_network']).decode('ascii').strip()
        require(owned(tag, network=True) == [network])

        PHASE = 'publish_maintenance'
        with edge.CONF.open('xb') as stream:
            staged.append(edge.CONF)
            stream.write(config)
        container = call(
            DOCKER, 'run', '--pull', 'never', '--detach', '--name', edge.NAME.lstrip('/'),
            '--label', f'{LABEL}={tag}', '--label', f'travel.edge.role={edge.ROLE}',
            '--network', plan['docker_network'], '--ip', plan['proxy_ip'], '-p', '0.0.0.0:443:443/tcp',
            '--user', '0:0', '--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=24m,mode=1777',
            '--memory', '160m', '--memory-swap', '160m', '--cpus', '0.25', '--pids-limit', '32',
            '--cap-drop', 'ALL', *(part for cap in capabilities for part in ('--cap-add',cap)),
            '--security-opt', 'no-new-privileges',
            '--mount', f'type=bind,source={edge.CONF},target={edge.TARGET_CONF},readonly',
            '--mount', f'type=bind,source={edge.STORE},target={edge.TARGET_STORE},readonly',
            '--entrypoint', 'nginx', image, '-c', edge.TARGET_CONF, '-g', 'daemon off;',
        ).decode('ascii').strip()
        require(owned(tag) == [container])
        pin = {'id': container, 'image': image, 'network': plan['docker_network'],
               'ip': plan['proxy_ip'], 'config_sha256': MAINTENANCE}
        with edge.PIN.open('xb') as stream:
            staged.append(edge.PIN)
            stream.write(json.dumps(pin).encode('ascii'))
        target = edge.Edge(edge.parse_target(edge.read_root_file(edge.PIN, 4096)))
        target.check()
        require(edge.rotate.wait_for_leaf(target.peer, selected))
        check_http()

        PHASE = 'external_confirmation'
        print(json.dumps({'event': 'ready', 'tag': tag, 'fingerprint_sha256': selected}), flush=True)
        ready, _, _ = select.select([sys.stdin], [], [], 42)
        require(ready and sys.stdin.readline(64).strip() == 'retain '+tag)
        require(target.guarded() == container and owned(tag) == [container])
        lineage = edge.tls.local_lineage(paired_key=True)
        require(lineage['status'] == 'ok' and lineage['fingerprint_sha256'] == selected and store.current() == selected)
        call(DOCKER, 'update', '--restart', 'unless-stopped', container)
        require(call(DOCKER, 'inspect', '--format', '{{.HostConfig.RestartPolicy.Name}}', container).strip() == b'unless-stopped')
        check_http()
        retained = True
        return {'event': 'retained', 'tag': tag, 'network_id': network, 'container_id': container, 'image_id': image,
                'fingerprint_sha256': selected, 'restart_policy': 'unless-stopped',
                'shared_http_matches': True}
    finally:
        if not retained:
            for container in owned(tag):
                call(DOCKER,'rm','-f',container)
            for network in owned(tag, network=True):
                call(DOCKER,'network','rm',network)
            for path in reversed(staged):
                path.unlink()
            require(not owned(tag) and not owned(tag, network=True))
            if selected != previous:
                store.point(previous)
            print(json.dumps({'event':'rolled_back','tag':tag,'resources_removed':True}),flush=True)


def run(tag, payload):
    require(os.geteuid() == 0 and sys.flags.isolated and re.fullmatch('[a-f0-9]{16}', tag))
    require(set(payload) == MODULES | {'maintenance.conf'})
    config = payload['maintenance.conf'].encode('utf-8')
    require(hashlib.sha256(config).hexdigest() == MAINTENANCE)
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='travel-publish-maintenance-'+tag+'-', dir='/run') as scratch:
        folder = Path(scratch)
        for name in MODULES:
            (folder/name).write_text(payload[name], encoding='utf-8')
        folder.chmod(0o711)
        helper = folder/'acme_probe_remote.py'
        helper.chmod(0o644)
        sys.path.insert(0, scratch)
        return publish_one(tag, config, helper)


def interrupted(*args):
    raise RuntimeError('Publication interrupted before verification')


if __name__ == '__main__':
    for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, interrupted)
    try:
        print(json.dumps(run(sys.argv[1], json.loads(sys.stdin.readline(96*1024)))),flush=True)
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps({'event':'failed','phase':PHASE,'error_type':type(error).__name__}),flush=True)
        raise SystemExit(1) from None
