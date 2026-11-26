"""Verify production travel ingress while the approved HTTPS edge serves maintenance."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys

if __package__:
    from . import travel_tls_edge as edge
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import travel_tls_edge as edge


APP = '28ba4881d397e73dd76bfed0ade8f8533d11510fb2a2f347192fd07bb76cc281'
WORK = f'/opt/travel/releases/{APP}'
PYTHON = f'/opt/travel/runtime-envs/{APP}/bin/python'
GATEWAY = '172.31.240.1'
PROXY = '172.31.240.2'
BACKEND = 'travel.service'
FILTER = 'travel-backend-ingress.service'
MAINTENANCE = 'e2ba23f487a2ede868983373fc3af0d60652c7b6ab4b1da4bc2d945240288e1e'
PINNED = {
    f'{WORK}/release.json': '63514d2d03011ba0404ce1de3bd3b65155532136cfa61f8424299ab4bba9a3fb',
    '/etc/systemd/system/travel.service': 'ec03ed87a56ea67acf870c71e6722fd3cd2e041af79e712b310cb3be4d36649e',
    '/etc/systemd/system/travel-backend-ingress.service': 'e63ce1ddd6f79bc1be6f50aa5a318c15c9c489cfbe90edc6db9c2489c811a92e',
    '/etc/travel-tls/backend-ingress.nft': '707816e51b6926eea6814c275d8420f131407d07337a54b66ad9ee274c5c3b89',
}


def run(*argv):
    return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=8).stdout


def properties(unit, *fields):
    return dict(line.split('=', 1) for line in run('/usr/bin/systemctl', 'show', '--no-pager',
                '--property=' + ','.join(fields), unit).decode().splitlines())


def process_identity():
    base = {'ActiveState': 'active', 'DropInPaths': '', 'NeedDaemonReload': 'no'}
    for unit, substate in ((BACKEND, 'running'), (FILTER, 'exited')):
        live = properties(unit, 'ActiveState', 'SubState', 'FragmentPath', 'DropInPaths', 'NeedDaemonReload')
        edge.require(live == {**base, 'SubState': substate, 'FragmentPath': f'/etc/systemd/system/{unit}'},
                     'Unexpected effective service or ingress unit')
    live = properties(BACKEND, 'MainPID', 'WorkingDirectory', 'Requires', 'ExecStart')
    pid = int(live['MainPID'])
    expected = f'{PYTHON} -B -m uvicorn app.main:app --host {GATEWAY} --port 8000 --workers 1 '
    expected += f'--proxy-headers --forwarded-allow-ips {PROXY}'
    edge.require(pid > 0 and live['WorkingDirectory'] == f'{WORK}/backend'
                 and FILTER in live['Requires'].split() and f'argv[]={expected} ;' in live['ExecStart'],
                 'Unexpected backend process invocation')
    rows = run('/usr/bin/ss', '-H', '-ltnp', 'sport', '=', ':8000').decode().splitlines()
    edge.require(len(rows) == 1 and rows[0].split()[3] == f'{GATEWAY}:8000'
                 and re.search(rf'\bpid={pid}\b', rows[0]), 'Unexpected TCP 8000 owner or listen address')
    return pid


def rule_counters(raw):
    def equal(lhs, value):
        return {'match': {'op': '==', 'left': lhs, 'right': value}}

    def payload(protocol, field):
        return {'payload': {'protocol': protocol, 'field': field}}

    base = {'family': 'inet', 'table': 'travel_guard'}
    objects = [item for item in json.loads(raw)['nftables'] if 'metainfo' not in item]
    table, chain, allow, deny = objects
    edge.require({key: val for key, val in table['table'].items() if key != 'handle'} ==
                 {'family': 'inet', 'name': 'travel_guard'}, 'Unexpected ingress table')
    edge.require({key: val for key, val in chain['chain'].items() if key != 'handle'} ==
                 {**base, 'name': 'backend_input', 'type': 'filter', 'hook': 'input', 'prio': -5, 'policy': 'accept'},
                 'Unexpected ingress chain')
    port = equal(payload('tcp', 'dport'), 8000)
    permitted = [equal({'meta': {'key': 'iifname'}}, 'br-travel-edge'), equal(payload('ip', 'saddr'), PROXY),
                 equal(payload('ip', 'daddr'), GATEWAY), port]
    packets = []
    for rule, matches, verdict in ((allow['rule'], permitted, 'accept'), (deny['rule'], [port], 'drop')):
        expr = rule['expr']
        edge.require({key: val for key, val in rule.items() if key not in ('handle', 'expr')} ==
                     {**base, 'chain': 'backend_input'} and expr[:-2] == matches and expr[-1] == {verdict: None},
                     'Unexpected active ingress rule')
        counter = expr[-2]['counter']['packets']
        edge.require(type(counter) is int and counter >= 0, 'Invalid ingress counter')
        packets.append(counter)
    return tuple(packets)


def live_counters():
    return rule_counters(run('/usr/sbin/nft', '--json', '-n', 'list', 'table', 'inet', 'travel_guard'))


def checked_edge():
    spec = edge.parse_target(edge.read_root_file(edge.PIN, 4096))
    edge.require(spec['config_sha256'] == MAINTENANCE and spec['network'] == 'travel-edge' and spec['ip'] == PROXY,
                 'The reviewed dedicated maintenance proxy is required')
    target = edge.Edge(spec)
    target.peer()
    return target


def proxy_read(target, path):
    peer = target.guarded()
    data = edge.docker('exec', peer, '/usr/bin/curl', '-q', '--noproxy', '*', '--silent', '--show-error',
                       '--max-time', '3', '--max-filesize', '4096', '--write-out', '\n%{http_code}',
                       f'http://{GATEWAY}:8000{path}')
    body, status = data.rsplit(b'\n', 1)
    return int(status), json.loads(body)


def verify_while_closed():
    for name, expected in PINNED.items():
        raw = edge.read_root_file(Path(name), 65536, public_certificate=True)
        edge.require(hashlib.sha256(raw).hexdigest() == expected, 'A reviewed runtime or configuration file differs')
    pid = process_identity()
    target = checked_edge()
    before_allow, before_deny = live_counters()
    edge.require(proxy_read(target, '/api/health') == (200, {'status': 'ok'}), 'Proxy could not read backend health')
    edge.require(proxy_read(target, '/api/trips')[0] == 401, 'Anonymous trip read was not denied')
    try:
        with socket.create_connection((GATEWAY, 8000), timeout=1.5):
            raise ValueError('Host could bypass the dedicated proxy')
    except TimeoutError:
        pass
    after_allow, after_deny = live_counters()
    edge.require(after_allow > before_allow and after_deny > before_deny, 'Expected allow and drop counters did not advance')
    edge.require(process_identity() == pid, 'Travel backend changed during the checks')
    target.peer()
    return {'state': 'maintenance_ingress_verified', 'application_id': APP,
            'dedicated_proxy_reached_backend': True, 'anonymous_trip_read_http': 401,
            'nonproxy_host_connection_dropped': True, 'https_travel_page_remained_maintenance': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect-live', action='store_true')
    args = parser.parse_args()
    if not args.inspect_live:
        print(json.dumps({'state': 'review_only', 'application_id': APP}))
        return 0
    try:
        edge.require(sys.platform == 'linux' and os.geteuid() == 0, 'Expected the approved Linux origin')
        result = verify_while_closed()
    except (KeyError, IndexError, TypeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({'state': 'ingress_unconfirmed', 'error_type': type(error).__name__}), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
