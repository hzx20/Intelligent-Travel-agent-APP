"""Create a bounded transient-service specification; never start a unit here."""
from pathlib import PurePosixPath
import re
import shlex

HASH = re.compile('[0-9a-f]{64}')
TEMP_ROOT = re.compile(r'/tmp/travel-release-rehearsal-\d{8}-[0-9a-f]{8}')
KEYS = {'Type', 'User', 'Group', 'WorkingDirectory', 'EnvironmentFile', 'Environment', 'UnsetEnvironment',
        'UMask', 'ExecStart', 'Restart', 'RestartSec', 'MemoryMax', 'NoNewPrivileges', 'PrivateTmp'}
PROVIDER_ENV = ('JWT_SECRET', 'AMAP_API_KEY', 'AMAP_JS_KEY', 'AMAP_JS_SECURITY_CODE', 'ZHIPU_API_KEY')


def temporary_path(value, base):
    path = PurePosixPath(value)
    if (not TEMP_ROOT.fullmatch(base) or not path.is_absolute() or str(path) != value
            or path == PurePosixPath(base) or not path.is_relative_to(PurePosixPath(base)) or '..' in value.split('/')
            or not re.fullmatch(r'[A-Za-z0-9_./-]+', value)):
        raise ValueError('Only canonical paths under the approved temporary root are supported')
    return value


def service_settings(text):
    selected = False
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('['):
            selected = line == '[Service]'
            continue
        if selected:
            if '=' not in line or line.endswith('\\'):
                raise ValueError('Unsupported service syntax')
            key, value = line.split('=', 1)
            values.setdefault(key, []).append(value)
    if set(values) != KEYS:
        raise ValueError('Unexpected service-profile directives')
    return values


def specification(meta, unit_text, base, stage, application_source, venv_source, nonce, port, sink_port):
    if not HASH.fullmatch(meta['id']) or not HASH.fullmatch(meta['application_id']) or not re.fullmatch('[0-9a-f]{32}', nonce):
        raise ValueError('Invalid reviewed identity or test nonce')
    for value in (stage, application_source, venv_source):
        temporary_path(value, base)
    if PurePosixPath(stage).parent != PurePosixPath(base) or stage == application_source or stage == venv_source:
        raise ValueError('Use a dedicated direct-child review directory')
    paths = list(map(PurePosixPath, (stage, application_source, venv_source)))
    if any(a.is_relative_to(b) or b.is_relative_to(a) for i, a in enumerate(paths) for b in paths[i + 1:]):
        raise ValueError('Test work, code and interpreter sources must be separate')
    if port == sink_port or any(not isinstance(p, int) or not 20000 <= p <= 65535 for p in (port, sink_port)):
        raise ValueError('Distinct non-production high ports required')
    values = service_settings(unit_text)
    app_id = meta['application_id']
    code = '/opt/travel/releases/' + app_id
    runtime = '/opt/travel/runtime-envs/' + app_id
    wanted = [runtime + '/bin/python', '-B', '-m', 'uvicorn', 'app.main:app', '--host', meta['backend_bind_ip'],
              '--port', '8000', '--workers', '1', '--proxy-headers', '--forwarded-allow-ips', meta['proxy_ip']]
    if (values['Type'] != ['simple'] or values['User'] != ['ubuntu'] or values['Group'] != ['ubuntu']
            or values['WorkingDirectory'] != [code + '/backend'] or values['EnvironmentFile'] != ['']
            or values['ExecStart'][0] != '' or len(values['ExecStart']) != 2 or shlex.split(values['ExecStart'][1]) != wanted
            or values['PrivateTmp'] != ['true'] or values['NoNewPrivileges'] != ['true'] or values['UMask'] != ['0077']
            or values['Restart'] != ['always'] or values['RestartSec'] != ['3'] or values['MemoryMax'] != ['1200M']
            or values['UnsetEnvironment'] != ['PYTHONPATH PYTHONHOME']):
        raise ValueError('The test must preserve the reviewed identity and execution controls')
    env = {}
    for value in values['Environment']:
        pieces = shlex.split(value)
        if len(pieces) != 1 or '=' not in pieces[0]:
            raise ValueError('Invalid profile environment')
        key, content = pieces[0].split('=', 1)
        if key in env:
            raise ValueError('Duplicate profile environment')
        env[key] = content
    expected_env = {'TRAVEL_ENV_FILE': '/opt/travel/app/.env', 'TRAVEL_DATA_DIR': '/opt/travel/app/backend/data',
                    'FORWARDED_ALLOW_IPS': meta['proxy_ip'], 'PYTHONUNBUFFERED': '1', 'PYTHONDONTWRITEBYTECODE': '1',
                    'TZ': 'Asia/Shanghai', 'TRAVEL_SHARED_LIMITS': '0', 'TRAVEL_SHARED_CACHE': '0', 'TRAVEL_PROVIDER_BUDGET': '0'}
    if env != expected_env:
        raise ValueError('Unexpected runtime environment in service profile')
    command = wanted[:]
    command[command.index('--host') + 1] = '127.0.0.1'
    command[command.index('--port') + 1] = str(port)
    work = stage + '/work'
    unset = ['PYTHONPATH', 'PYTHONHOME', *PROVIDER_ENV, *(name.lower() for name in PROVIDER_ENV)]
    readonly = [(application_source, code), (venv_source, runtime),
                (work + '/synthetic.env', env['TRAVEL_ENV_FILE']), (work + '/probe', '/opt/travel/qa/probe')]
    writable = [(work + '/data', env['TRAVEL_DATA_DIR'])]
    properties = {
        'User': 'ubuntu', 'Group': 'ubuntu', 'WorkingDirectory': code + '/backend',
        'UMask': '0077', 'Restart': 'always', 'RestartSec': '3', 'NoNewPrivileges': 'true', 'PrivateTmp': 'true',
        'MemoryMax': '384M', 'CPUQuota': '25%', 'TasksMax': '64', 'TimeoutStopSec': '10s', 'KillMode': 'control-group',
        'RuntimeMaxSec': '120s', 'StartLimitIntervalSec': '300s', 'StartLimitBurst': '2',
        'IPAddressDeny': 'any', 'IPAddressAllow': 'localhost', 'UnsetEnvironment': ' '.join(unset),
        'TemporaryFileSystem': '/opt/travel:rw,size=16m',
        # systemd-run v255 otherwise resolves the executable on the host before
        # creating the namespace. Keep the actual absolute ExecStart unchanged.
        'ExecSearchPath': runtime + '/bin',
        'BindReadOnlyPaths': ' '.join(a + ':' + b for a, b in readonly),
        'BindPaths': ' '.join(a + ':' + b for a, b in writable),
        'ExecStartPre': runtime + '/bin/python -B /opt/travel/qa/probe/preflight.py /opt/travel/qa/probe/config.json',
    }
    return {'unit_name': 'travel-qa-' + nonce + '.service', 'description': 'Travel isolated QA ' + nonce,
            'plan_id': meta['id'], 'application_id': app_id, 'properties': properties, 'environment': env,
            'command': command, 'formal_code': code, 'formal_runtime': runtime, 'readonly_maps': readonly,
            'writable_maps': writable, 'port': port, 'sink_ip': meta['backend_bind_ip'], 'sink_port': sink_port,
            'qa_differences': ['Loopback/random high port', 'Namespace-only synthetic /opt/travel and data/env mappings',
                               'Additional localhost-only network filter and preflight', '384M/25% CPU/64 tasks test caps',
                               'Bounded runtime/two starts/stop timeout; transient collection']}


def run_arguments(spec):
    if not re.fullmatch('travel-qa-[0-9a-f]{32}\\.service', spec['unit_name']):
        raise ValueError('Only the dedicated transient test namespace may be started')
    args = ['sudo', '-n', 'systemd-run', '--quiet', '--collect', '--service-type=simple',
            '--unit=' + spec['unit_name'], '--description=' + spec['description']]
    args.extend('--property=' + key + '=' + value for key, value in spec['properties'].items())
    args.extend('--setenv=' + key + '=' + value for key, value in spec['environment'].items())
    return [*args, '--', *spec['command']]


def crash_arguments(spec):
    if not re.fullmatch('travel-qa-[0-9a-f]{32}\\.service', spec['unit_name']):
        raise ValueError('Only the owned temporary service may be interrupted')
    return ['sudo', '-n', 'systemctl', 'kill', '--kill-whom=main', '--signal=SIGKILL', spec['unit_name']]
