"""Read back a scheduled travel certificate load using installed host commands."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from shlex import join
import subprocess

if __package__:
    from .recheck_travel_maintenance import TARGET, http_readback, observed, publication
    from .travel_tls_edge import REVIEWED_CONFIG
else:
    from recheck_travel_maintenance import TARGET, http_readback, observed, publication
    from travel_tls_edge import REVIEWED_CONFIG


SERVICE = 'travel-ip-renew.service'
TIMER = 'travel-ip-renew.timer'
POST_CHECK = ['/usr/bin/python3', '-I', '-B', '/usr/local/libexec/travel_tls_check.py', 'saved', '--pair']
POST_LOAD = ['/usr/bin/python3', '-I', '-B', '/usr/local/libexec/travel_tls_edge.py', 'reconcile', '--apply']
BUS = 'busctl --json=short get-property org.freedesktop.systemd1'
SERVICE_POST = f'{BUS} /org/freedesktop/systemd1/unit/travel_2dip_2drenew_2eservice org.freedesktop.systemd1.Service ExecStartPost'
TIMER_TRIGGER = f'{BUS} /org/freedesktop/systemd1/unit/travel_2dip_2drenew_2etimer org.freedesktop.systemd1.Timer LastTriggerUSec'
SERVICE_STATUS = f'systemctl show --no-pager -p ActiveState,Result,NeedDaemonReload,DropInPaths,OnFailure {SERVICE}'
HOOK_PATH = '/etc/systemd/system/travel-ip-renew.service.d/90-edge-load.conf'
ALERT_PATH = '/etc/systemd/system/travel-ip-renew.service.d/95-mail-alert.conf'
ALERT_SHA256 = '69c8018160c697886134c5eab8e447c9e102f061cdb92d16e327d9dde2335719'
EDGE_IDENTITY = ('import sys; sys.path.insert(0,"/usr/local/libexec"); import travel_tls_edge as edge; '
                 'spec=edge.parse_target(edge.read_root_file(edge.PIN,4096)); '
                 'print(edge.Edge(spec).guarded()); print(spec["config_sha256"])')


def read_origin(command):
    result = subprocess.run([*publication.probe.SSH, command], capture_output=True, check=True, timeout=18)
    return result.stdout.decode('utf-8')


def classify_run(trigger, status, commands):
    expected = [[args[0], args, False] for args in (POST_CHECK, POST_LOAD)]
    approved_dropins = {
        HOOK_PATH: '',
        f'{HOOK_PATH} {ALERT_PATH}': 'travel-ip-alert@renewal.service',
    }
    dropins = status.get('DropInPaths')
    if (dropins not in approved_dropins or status.get('OnFailure') != approved_dropins[dropins]
            or status.get('NeedDaemonReload') != 'no'):
        raise ValueError('The approved automatic load configuration is not active')
    if [command[:3] for command in commands] != expected or any(len(command) != 10 for command in commands):
        raise ValueError('Unexpected renewal post-command order or arguments')
    if status.get('ActiveState') in ('active', 'activating', 'deactivating'):
        return 'check_running'
    if status.get('Result') != 'success' or status.get('ActiveState') != 'inactive':
        return 'scheduled_check_failed'
    check, load = commands
    if not load[3]:
        return 'waiting_for_first_hook_execution'
    if not (0 < trigger <= check[3] <= check[5] <= load[3] <= load[5] <= trigger + 240_000_000):
        return 'last_hook_could_not_be_tied_to_timer'
    if not load[7] or check[8:] != [1, 0] or load[8:] != [1, 0]:
        return 'scheduled_check_failed'
    return 'hook_completed'


def snapshot():
    output = read_origin(f'set -e; {TIMER_TRIGGER}; {SERVICE_POST}; {SERVICE_STATUS}; {TIMER_TRIGGER}')
    first, post, *properties, last = output.splitlines()
    trigger = json.loads(first)['data']
    if type(trigger) is not int or trigger < 0 or first != last:
        raise ValueError('Timer changed while reading renewal status')
    status = dict(line.split('=', 1) for line in properties)
    if ALERT_PATH in status['DropInPaths'].split():
        digest = read_origin(f'sudo -n /usr/bin/sha256sum {ALERT_PATH}').split()
        if digest != [ALERT_SHA256, ALERT_PATH]:
            raise ValueError('Renewal mail configuration differs from the reviewed unit')
    commands = json.loads(post)['data']
    return trigger, status, commands


def hook_outcome(load):
    start, stop, pid = int(load[3]), int(load[5]), int(load[7])
    command = ('sudo -n /usr/bin/journalctl --no-pager --quiet -o json --output-fields=MESSAGE '
               f'--since=@{start // 1_000_000} --until=@{stop // 1_000_000 + 1} -n 5 '
               f'_SYSTEMD_UNIT={SERVICE} _PID={pid}')
    messages = [json.loads(json.loads(line)['MESSAGE']) for line in read_origin(command).splitlines()]
    if messages == [{'state': 'unchanged', 'reload_sent': False, 'rollback_verified': False}]:
        return 'unchanged'
    if messages == [{'state': 'switched', 'reload_sent': True, 'rollback_verified': False}]:
        return 'switched'
    raise ValueError('The hook journal does not contain one unambiguous success result')


def edge_identity():
    identity = join(('sudo', '-n', '/usr/bin/python3', '-I', '-B', '-c', EDGE_IDENTITY))
    output = read_origin('set -e; sudo -n /usr/bin/readlink /etc/travel-tls/edge/current; '
                         'sudo -n /usr/bin/openssl x509 -in /etc/travel-tls/acme/live/travel-ip-public/cert.pem '
                         f'-noout -sha256 -fingerprint; {identity}')
    selection, fingerprint, container, config = output.splitlines()
    leaf = fingerprint.split('=', 1)[1].replace(':', '').lower()
    if not re.fullmatch('[a-f0-9]{64}', leaf) or selection != f'generations/{leaf}':
        raise ValueError('Saved certificate and selected generation differ')
    if not re.fullmatch('[a-f0-9]{64}', container) or config not in REVIEWED_CONFIG:
        raise ValueError('The active proxy does not match a reviewed configuration')
    return container, config, leaf


def verify_public(interface):
    identity = edge_identity()
    _, config, leaf = identity
    mode = REVIEWED_CONFIG[config]
    for path in ('/travel/', '/travel/api/health'):
        route = observed(path, 443, interface, expected_leaf=leaf)
        if mode == 'normal' and route.get('status') != 200:
            raise ValueError('Expected the current certificate and a healthy public travel route')
        if mode == 'maintenance' and (route.get('status'), route.get('retry_after'), route.get('no_store')) != (503, '60', True):
            raise ValueError('Expected the current certificate and maintenance response on both travel routes')
    if observed('/', 443, interface, expected_leaf=leaf).get('status') != 404:
        raise ValueError('Unexpected non-travel HTTPS response')
    (root, _), (travel, location) = http_readback(interface)
    redirect = sha256(f'https://{TARGET}/travel/'.encode('ascii')).hexdigest()
    if root == 200 and travel == 308 and location == redirect:
        if observed('/travel/api/health', 80, interface).get('status') != 403:
            raise ValueError('The legacy HTTP API is not blocked')
        http_mode = 'https_redirect'
    elif root == 200 and travel == 200 and mode == 'maintenance':
        http_mode = 'legacy'
    else:
        raise ValueError('Public HTTP routes differ from the reviewed proxy mode')
    if edge_identity() != identity:
        raise ValueError('Proxy or certificate changed during public verification')
    report = {'saved_selected_and_public_leaf_sha256': leaf, 'public_edge_mode': mode,
              'public_http_mode': http_mode, 'public_routes_verified': True}
    if http_mode == 'legacy':
        report['public_maintenance_and_legacy_http_verified'] = True
    return report


def inspect(interface):
    trigger, status, commands = snapshot()
    state = classify_run(trigger, status, commands)
    report = {'state': state, 'timer_trigger_utc': datetime.fromtimestamp(trigger / 1e6, timezone.utc).isoformat() if trigger else None}
    if state != 'hook_completed':
        return report
    outcome = hook_outcome(commands[1])
    public = verify_public(interface)
    if snapshot() != (trigger, status, commands):
        raise ValueError('Renewal state changed during verification')
    return {**report, 'state': 'scheduled_hook_verified', 'hook_outcome': outcome, 'reload_sent': outcome == 'switched',
            **public}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect-origin', action='store_true')
    parser.add_argument('--interface', type=int)
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    if not args.inspect_origin:
        if args.receipt:
            parser.error('Saving a receipt requires inspecting the origin')
        print(json.dumps({'state': 'review_only'}))
        return 0
    try:
        report = inspect(args.interface)
        if args.receipt:
            with args.receipt.open('x', encoding='utf-8') as file:
                json.dump(report, file, indent=2)
                file.write('\n')
        print(json.dumps(report))
    except (OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({'state': 'verification_unconfirmed', 'error_type': type(error).__name__}))
        return 1
    return int(report['state'] not in ('scheduled_hook_verified', 'waiting_for_first_hook_execution', 'check_running'))


if __name__ == '__main__':
    raise SystemExit(main())
