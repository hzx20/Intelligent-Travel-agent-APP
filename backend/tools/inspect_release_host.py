"""Inventory the travel release host. Read operational metadata only, not secrets or data."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys


UNIT = 'travel.service'
PROPERTIES = ('Id', 'LoadState', 'ActiveState', 'SubState', 'FragmentPath', 'DropInPaths', 'NeedDaemonReload',
              'MainPID', 'User', 'Group', 'WorkingDirectory', 'ExecStart', 'EnvironmentFiles', 'Type',
              'UMask', 'PrivateTmp', 'NoNewPrivileges', 'MemoryMax', 'Restart', 'RestartUSec')
TRAVEL = Path('/opt/travel')
NGINX_CONFIG = Path('/opt/continew/nginx.conf')
NGINX_WEB = Path('/opt/continew/web')
DOCKER = ('sudo', '-n', 'docker')


def run(*args):
    p = subprocess.run(args, capture_output=True, text=True, timeout=8, env={**os.environ, 'LC_ALL': 'C'})
    if p.returncode:
        # Output can contain unrelated system configuration; do not forward it.
        raise RuntimeError(f'{args[0]} query failed with status {p.returncode}')
    return p.stdout.strip()


def unit_properties():
    text = run('systemctl', 'show', '--all', '--no-pager', '--property=' + ','.join(PROPERTIES), UNIT)
    values = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
    required = set(PROPERTIES) - {'EnvironmentFiles'}
    if values.get('Id') != UNIT or values.get('LoadState') != 'loaded' or not required <= values.keys():
        raise RuntimeError('Incomplete or unexpected travel service metadata')
    return values


def path_report(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {'exists': False}
    kind = next((name for name, test in (('directory', stat.S_ISDIR), ('file', stat.S_ISREG),
                                         ('symlink', stat.S_ISLNK)) if test(info.st_mode)), 'other')
    return {'exists': True, 'kind': kind, 'canonical': path.resolve() == path,
            'uid': info.st_uid, 'gid': info.st_gid, 'mode': f'{stat.S_IMODE(info.st_mode):04o}',
            'inspector_readable': os.access(path, os.R_OK), 'inspector_writable': os.access(path, os.W_OK),
            'inspector_searchable_or_executable': os.access(path, os.X_OK)}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def unit_summary(values, release_id):
    commands = re.findall(r'argv\[\]=([^;]*?)\s*;', values['ExecStart'])
    args = shlex.split(commands[0]) if len(commands) == 1 else []
    executable = args[0] if args else None
    def explicit_flag(flag, expected):
        return any(args[i + 1] == expected for i, arg in enumerate(args[:-1]) if arg == flag)

    fragment = Path(values['FragmentPath'])
    return {'active_state': values['ActiveState'], 'sub_state': values['SubState'], 'main_pid': int(values['MainPID']),
            'fragment_is_expected_system_file': fragment == Path('/etc/systemd/system/travel.service'),
            'fragment_sha256': digest(fragment) if fragment == Path('/etc/systemd/system/travel.service') else None,
            'drop_ins_present': bool(values['DropInPaths']), 'need_daemon_reload': values['NeedDaemonReload'],
            'environment_files_present': bool(values.get('EnvironmentFiles')), 'identity_is_ubuntu': values['User'] == values['Group'] == 'ubuntu',
            'working_directory_is_legacy': values['WorkingDirectory'] == '/opt/travel/app/backend',
            'working_directory_matches_release': values['WorkingDirectory'] == str(TRAVEL / 'releases' / release_id / 'backend'),
            'uses_legacy_runtime': executable == '/opt/travel/venv/bin/python',
            'uses_release_runtime': executable == str(TRAVEL / 'runtime-envs' / release_id / 'bin/python'),
            'explicit_worker_count_one': explicit_flag('--workers', '1'),
            'explicit_wildcard': explicit_flag('--host', '0.0.0.0'),
            'type': values['Type'], 'umask': values['UMask'], 'private_tmp': values['PrivateTmp'],
            'no_new_privileges': values['NoNewPrivileges'], 'memory_max': values['MemoryMax'],
            'restart': values['Restart'], 'restart_delay': values['RestartUSec']}


def nginx_summary():
    target = 'continew-web'
    networks = json.loads(run(*DOCKER, 'inspect', '--format', '{{json .NetworkSettings.Networks}}', target))
    mounts = json.loads(run(*DOCKER, 'inspect', '--format', '{{json .Mounts}}', target))
    get_mounts = lambda dst: [m for m in mounts if m['Destination'] == dst]
    config = get_mounts('/etc/nginx/nginx.conf')
    web = get_mounts('/usr/share/nginx/html')
    travel_mount = get_mounts('/usr/share/nginx/html/travel')
    source_sha = digest(NGINX_CONFIG)
    container_sha = run(*DOCKER, 'exec', target, 'sha256sum', '/etc/nginx/nginx.conf').split()[0]
    if not re.fullmatch('[a-f0-9]{64}', container_sha):
        raise RuntimeError('Invalid container hash')
    network = next(iter(networks.values())) if len(networks) == 1 else None
    return {'single_network': network is not None,
            'proxy_ip': str(ipaddress.IPv4Address(network['IPAddress'])) if network else None,
            'gateway': str(ipaddress.IPv4Address(network['Gateway'])) if network else None,
            'source_config_sha256': source_sha, 'container_file_sha256': container_sha,
            'matching_config_bytes': source_sha == container_sha,
            'config_bind_matches': len(config) == 1 and config[0]['Type'] == 'bind' and config[0]['Source'] == str(NGINX_CONFIG),
            'config_bind_read_only': len(config) == 1 and config[0]['RW'] is False,
            'web_bind_matches': len(web) == 1 and web[0]['Type'] == 'bind' and web[0]['Source'] == str(NGINX_WEB),
            'direct_travel_mount_present': bool(travel_mount)}


def inspect(release_id):
    if not re.fullmatch('[0-9a-f]{64}', release_id) or sys.platform != 'linux' or os.geteuid() == 0:
        raise ValueError('Run on Linux as the regular SSH account, with a SHA256 release ID')
    values = unit_properties()
    unit = unit_summary(values, release_id)
    code, runtime = TRAVEL / 'releases', TRAVEL / 'runtime-envs'
    data = TRAVEL / 'app/backend/data'
    paths = (Path('/etc/systemd/system/travel.service'), Path('/opt'), TRAVEL, code, code / release_id, runtime, runtime / release_id,
             TRAVEL / 'app', TRAVEL / 'app/backend', TRAVEL / 'app/.env', data, data / 'app.db', data / '.jwt_secret',
             NGINX_WEB, NGINX_WEB / 'travel')
    metadata = {str(path): path_report(path) for path in paths}
    sockets = run('ss', '-H', '-ltn', 'sport = :8000')
    listen = sorted({line.split()[3] for line in sockets.splitlines()})
    nginx = nginx_summary()
    if values != unit_properties() or unit != unit_summary(values, release_id) or digest(NGINX_CONFIG) != nginx['source_config_sha256']:
        raise RuntimeError('Service or nginx changed during observation')
    return {'release_id': release_id, 'inspector_uid': os.geteuid(), 'inspector_gid': os.getegid(),
            'unit': unit, 'listeners_8000': listen, 'paths': metadata, 'nginx': nginx, 'mutated_host': False}


if __name__ == '__main__':
    try:
        print(json.dumps(inspect(sys.argv[1]), sort_keys=True))
    except (IndexError, ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        raise SystemExit(f'Host inventory incomplete ({type(error).__name__})') from None
