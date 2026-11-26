"""Root, stdlib-only inspection of one uniquely described transient QA service."""
import argparse
import json
import os
from pathlib import Path
import pwd
import re
import subprocess

PROPS = ('Id', 'LoadState', 'ActiveState', 'SubState', 'Transient', 'FragmentPath', 'Description', 'User', 'Group',
         'WorkingDirectory', 'ExecStart', 'EnvironmentFiles', 'MainPID', 'ControlGroup', 'NRestarts', 'PrivateTmp',
         'NoNewPrivileges', 'UMask', 'MemoryMax', 'Restart', 'Result', 'ExecSearchPath')


def parse_properties(text):
    props = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
    # systemctl v255 omits an empty EnvironmentFiles array even with --all.
    props.setdefault('EnvironmentFiles', '')
    return props


def cgroup_matches(props, unit, pid):
    if props['ControlGroup'] == '/system.slice/' + unit:
        return True
    if pid or props['ControlGroup']:
        return False
    return props['ActiveState'] in ('inactive', 'failed') or (
        props['ActiveState'] == 'activating' and props['SubState'] == 'auto-restart')


def request(path):
    path = Path(path).absolute(); root = path.parent
    assert path.resolve() == path and path.name == 'request.json'
    assert re.fullmatch(r'/tmp/travel-release-rehearsal-\d{8}-[0-9a-f]{8}', str(root.parent))
    assert path.stat().st_uid == pwd.getpwnam('ubuntu').pw_uid
    data = json.loads(path.read_text())
    assert data['stage'] == str(root) and data['work'] == str(root / 'work')
    spec = data['spec']
    assert re.fullmatch('travel-qa-[0-9a-f]{32}\\.service', spec['unit_name'])
    nonce = spec['unit_name'][len('travel-qa-'):-len('.service')]
    assert spec['description'] == 'Travel isolated QA ' + nonce
    assert re.fullmatch('[0-9a-f]{64}', spec['application_id'])
    assert spec['formal_code'] == '/opt/travel/releases/' + spec['application_id']
    assert spec['formal_runtime'] == '/opt/travel/runtime-envs/' + spec['application_id']
    for key in ('application_source', 'venv_source'):
        source = Path(data[key]); assert source.resolve() == source and source.is_relative_to(root.parent)
    return data


def inspect(data, details=True):
    spec = data['spec']; unit = spec['unit_name']
    result = subprocess.run(['systemctl', 'show', '--all', unit, '--property=' + ','.join(PROPS)],
                            capture_output=True, text=True, timeout=8)
    props = parse_properties(result.stdout)
    if props.get('LoadState') == 'not-found':
        return {'loaded': False, 'unit_name': unit, 'load_state': 'not-found', 'main_pid': 0}
    assert result.returncode == 0 and props.get('Id') == unit and props.get('Description') == spec['description']
    assert props.get('Transient') == 'yes' and props.get('FragmentPath') in ('', '/run/systemd/transient/' + unit)
    assert props['User'] == props['Group'] == 'ubuntu'
    assert props['WorkingDirectory'] == spec['formal_code'] + '/backend'
    assert spec['formal_runtime'] + '/bin/python' in props['ExecStart']
    assert props['ExecSearchPath'] == spec['formal_runtime'] + '/bin'
    assert '--host 127.0.0.1' in props['ExecStart'] and '--port ' + str(spec['port']) in props['ExecStart']
    assert props['EnvironmentFiles'] == ''
    assert props['PrivateTmp'] == props['NoNewPrivileges'] == 'yes' and props['UMask'] == '0077'
    assert props['MemoryMax'] == str(384 * 1024 * 1024) and props['Restart'] == 'always'
    pid = int(props.get('MainPID', '0'))
    assert cgroup_matches(props, unit, pid)
    summary = {'loaded': True, 'unit_name': unit, 'main_pid': pid, 'active_state': props['ActiveState'],
               'sub_state': props['SubState'], 'n_restarts': int(props.get('NRestarts', '0')), 'ownership_guard': True,
               'private_tmp_property': True, 'no_new_privileges_property': True, 'result': props.get('Result')}
    if not details or not pid:
        return summary
    assert pid > 1
    proc = Path('/proc') / str(pid)
    status = dict(line.split(':', 1) for line in (proc / 'status').read_text().splitlines() if ':' in line)
    account = pwd.getpwnam('ubuntu')
    assert all(int(v) == account.pw_uid for v in status['Uid'].split())
    assert all(int(v) == account.pw_gid for v in status['Gid'].split())
    assert status['NoNewPrivs'].strip() == '1' and status['Umask'].strip() == '0077'
    root = proc / 'root'
    host_data = Path(data['work']) / 'data'
    mapped_data = root / 'opt/travel/app/backend/data'
    host_env = Path(data['work']) / 'synthetic.env'
    mapped_env = root / 'opt/travel/app/.env'
    def identity(path):
        info = path.stat(); return (info.st_dev, info.st_ino)
    assert identity(host_data) == identity(mapped_data)
    assert identity(host_env) == identity(mapped_env)
    main = root / spec['formal_code'].lstrip('/') / 'backend/app/main.py'
    assert identity(main) == identity(Path(data['application_source']) / 'backend/app/main.py')
    assert not (root / data['host_sentinel'].lstrip('/')).exists()
    mounts = {}
    wanted = {'/opt/travel', *[b for _, b in spec['readonly_maps']], *[b for _, b in spec['writable_maps']]}
    for line in (proc / 'mountinfo').read_text().splitlines():
        fields = line.split()
        if fields[4] in wanted:
            separator = fields.index('-')
            mounts[fields[4]] = {'options': fields[5].split(','), 'filesystem': fields[separator + 1]}
    assert mounts['/opt/travel']['filesystem'] == 'tmpfs'
    assert all('ro' in mounts[b]['options'] for _, b in spec['readonly_maps'])
    assert all('rw' in mounts[b]['options'] for _, b in spec['writable_maps'])
    group = Path('/sys/fs/cgroup') / props['ControlGroup'].lstrip('/')
    cpu = (group / 'cpu.max').read_text().split()
    assert cpu[0] != 'max' and 0 < int(cpu[0]) / int(cpu[1]) <= .251
    assert (group / 'memory.max').read_text().strip() == str(384 * 1024 * 1024)
    assert (group / 'pids.max').read_text().strip() == '64'
    summary.update({'uid': account.pw_uid, 'gid': account.pw_gid, 'process_no_new_privileges': True,
                    'process_umask_0077': True, 'mapped_data_and_env_are_owned_synthetic_sources': True,
                    'mapped_application_identity_matches': True, 'private_tmp_hides_host_sentinel': True,
                    'project_root_is_namespace_tmpfs': True, 'readonly_maps_verified': len(spec['readonly_maps']),
                    'writable_maps_verified': len(spec['writable_maps']), 'cpu_quota_ratio': int(cpu[0]) / int(cpu[1]),
                    'memory_limit_bytes': 384 * 1024 * 1024, 'tasks_limit': 64,
                    'memory_current_bytes': int((group / 'memory.current').read_text())})
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True)
    parser.add_argument('--ownership-only', action='store_true')
    args = parser.parse_args()
    print(json.dumps(inspect(request(args.request), details=not args.ownership_only)))
