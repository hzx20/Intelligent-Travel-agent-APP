"""Run as ExecStartPre inside the test namespace, before importing the real app."""
import errno
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import time


def identity(path):
    value = Path(path).stat()
    return [value.st_dev, value.st_ino]


def run(config_file):
    if config_file != '/opt/travel/qa/probe/config.json':
        raise ValueError('This probe only runs through the dedicated namespace mapping')
    config = json.loads(Path(config_file).read_text())
    env_file = Path('/opt/travel/app/.env')
    data_dir = Path('/opt/travel/app/backend/data')
    # Verify mapped identities before opening any dotenv or application data.
    assert identity(env_file) == config['synthetic_env_identity']
    assert identity(data_dir) == config['synthetic_data_identity']
    assert identity(config['formal_code'] + '/backend/app/main.py') == config['application_main_identity']
    assert identity(config['formal_runtime'] + '/pyvenv.cfg') == config['venv_config_identity']
    report = {'success': False, 'pid': os.getpid(), 'uid': os.getuid(), 'gid': os.getgid()}
    try:
        assert hashlib.sha256(env_file.read_bytes()).hexdigest() == config['synthetic_env_sha256']
        assert sys.prefix == config['formal_runtime']
        assert not Path(config['host_sentinel']).exists()
        assert os.readlink('/proc/self/ns/net') == config['host_network_namespace']
        status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
        assert status['NoNewPrivs'].strip() == '1' and status['Umask'].strip() == '0077'
        assert os.getuid() == config['uid'] and os.getgid() == config['gid']
        assert all(os.environ.get(k) == v for k, v in config['expected_environment'].items())
        forbidden = {'jwt_secret', 'amap_api_key', 'amap_js_key', 'amap_js_security_code', 'zhipu_api_key', 'pythonpath', 'pythonhome'}
        assert not any(k.lower() in forbidden for k in os.environ)
        started = time.monotonic()
        with socket.socket() as sock:
            sock.settimeout(.6)
            try:
                result = sock.connect_ex((config['sink_ip'], config['sink_port']))
            except TimeoutError:
                result = errno.ETIMEDOUT
            except OSError as error:
                result = error.errno
        assert result in (errno.EPERM, errno.EACCES, errno.ETIMEDOUT, errno.EAGAIN, errno.EWOULDBLOCK)
        report.update({'success': True, 'synthetic_mapping_identities_match': True,
                       'synthetic_env_hash_matches': True, 'private_tmp_hides_host_sentinel': True,
                       'same_network_namespace_as_parent': True, 'no_new_privileges': True, 'umask_0077': True,
                       'expected_environment_matches': True, 'inherited_provider_variables_absent': True,
                       'non_loopback_owned_sink_unreachable': True, 'sink_connect_errno': result,
                       'sink_connect_elapsed_seconds': round(time.monotonic() - started, 3),
                       'sys_prefix_matches_mapped_runtime': True})
    finally:
        # The target directory was identified as the owned synthetic bind above.
        (data_dir / ('preflight-' + str(os.getpid()) + '.json')).write_text(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    run(sys.argv[1])
