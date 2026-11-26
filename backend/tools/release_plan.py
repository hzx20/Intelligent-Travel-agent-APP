"""Bind code, offline wheels, service and private nginx review pieces; never deploy."""
import argparse
import ast
from email import policy
from email.parser import BytesParser
import io
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
from urllib.parse import unquote, urlsplit
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.frontend_release import child, digest, identifier, root_path
from tools.nginx_merge import merge
from tools.release_candidate import verify as verify_application
from tools.travel_maintenance import render as render_maintenance

HELPERS = {'release_plan.py', 'release_candidate.py', 'frontend_release.py', 'deployment_check.py', 'backup_db.py',
           'nginx_merge.py', 'travel_maintenance.py'}
OPS = {'nginx-before.conf', 'nginx-normal.conf', 'nginx-maintenance.conf', 'service-profile.conf.template',
       'travel.service', 'requirements-linux.lock', 'target-tags.json', 'wheels.json', 'RELEASE_RUNBOOK.md'}
KEYS = {'format', 'kind', 'target', 'application_id', 'frontend_id', 'proxy_ip', 'backend_bind_ip', 'files', 'id'}
HASH = re.compile('[0-9a-f]{64}')
PIN = re.compile(r'([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[([A-Za-z0-9_,.-]+)\])?==([A-Za-z0-9_.+!-]+)')


def normalized(name):
    return re.sub('[-_.]+', '-', name).lower()


def pins(data, hashed=False):
    result = {}
    for raw in data.decode('utf-8-sig').splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        if hashed:
            parts = line.split(' --hash=sha256:')
            if len(parts) != 2 or not HASH.fullmatch(parts[1]):
                raise ValueError('Every offline requirement needs one exact SHA256')
            line, checksum = parts
        else:
            checksum = None
        match = PIN.fullmatch(line)
        if not match:
            raise ValueError('Only exact pinned requirements are supported')
        name, extras, version = match.groups()
        name = normalized(name)
        if name in result:
            raise ValueError('Duplicate normalized dependency')
        result[name] = {'version': version, 'extras': sorted({normalized(e) for e in (extras or '').split(',') if e}),
                        'sha256': checksum}
    if not result:
        raise ValueError('Empty dependency list')
    return result


def private_ip(value):
    address = ipaddress.ip_address(value)
    if address.version != 4 or not any(address in ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')):
        raise ValueError('Use a verified private IPv4 bridge/container address')
    return str(address)


def render_service(template, application_id, proxy_ip, backend_ip):
    proxy_ip, backend_ip = private_ip(proxy_ip), private_ip(backend_ip)
    if proxy_ip == backend_ip or not HASH.fullmatch(application_id):
        raise ValueError('Distinct network addresses and a valid application identity required')
    fields = set(re.findall('@([A-Z_]+)@', template))
    if fields != {'RELEASE_ID', 'PROXY_IP', 'BACKEND_BIND_IP'}:
        raise ValueError('Unexpected or missing service profile placeholders')
    rendered = template.replace('@RELEASE_ID@', application_id).replace('@PROXY_IP@', proxy_ip).replace('@BACKEND_BIND_IP@', backend_ip)
    values = {}
    section = None
    clean = []
    for raw in rendered.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('['):
            if line != '[Service]' or section is not None:
                raise ValueError('Only one reviewed Service section is supported')
            section = 'Service'; clean.append(line); continue
        if section is None or '=' not in line or line.endswith('\\'):
            raise ValueError('Unsupported service profile syntax')
        key, value = line.split('=', 1)
        values.setdefault(key, []).append(value)
        clean.append(line)
    expected = {
        'Type': ['simple'], 'User': ['ubuntu'], 'Group': ['ubuntu'],
        'WorkingDirectory': ['/opt/travel/releases/' + application_id + '/backend'], 'EnvironmentFile': [''],
        'Environment': ['"TRAVEL_ENV_FILE=/opt/travel/app/.env"', '"TRAVEL_DATA_DIR=/opt/travel/app/backend/data"',
                        '"FORWARDED_ALLOW_IPS=' + proxy_ip + '"', '"PYTHONUNBUFFERED=1"', '"PYTHONDONTWRITEBYTECODE=1"',
                        '"TZ=Asia/Shanghai"', '"TRAVEL_SHARED_LIMITS=0"', '"TRAVEL_SHARED_CACHE=0"', '"TRAVEL_PROVIDER_BUDGET=0"'],
        'UnsetEnvironment': ['PYTHONPATH PYTHONHOME'], 'UMask': ['0077'],
        'ExecStart': ['', '/opt/travel/runtime-envs/' + application_id + '/bin/python -B -m uvicorn app.main:app --host '
                      + backend_ip + ' --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips ' + proxy_ip],
        'Restart': ['always'], 'RestartSec': ['3'], 'MemoryMax': ['1200M'],
        'NoNewPrivileges': ['true'], 'PrivateTmp': ['true'],
    }
    if values != expected:
        raise ValueError('Service profile differs from the reviewed release controls')
    return ('# Generated review unit; not installed or started by this tool.\n'
            '[Unit]\nDescription=Travel planning API\nAfter=network.target\n\n'
            + '\n'.join(clean) + '\n\n[Install]\nWantedBy=multi-user.target\n')


def expanded_tag(value):
    parts = value.split('-')
    if len(parts) != 3 or not all(re.fullmatch('[a-zA-Z0-9_.]+', part) for part in parts):
        raise ValueError('Invalid wheel compatibility tag')
    return {a + '-' + b + '-' + c for a in parts[0].split('.') for b in parts[1].split('.') for c in parts[2].split('.')}


def verify_wheel(data, entry, pin, target_tags):
    filename = entry['filename']
    if PurePosixPath(filename).name != filename or '\\' in filename or ':' in filename or not filename.endswith('.whl'):
        raise ValueError('Unsafe wheel filename')
    if digest(data) != pin['sha256'] or entry['sha256'] != pin['sha256'] or len(data) != entry['bytes']:
        raise ValueError('Wheel checksum or size mismatch')
    pieces = filename[:-4].split('-')
    if len(pieces) not in (5, 6) or normalized(pieces[0]) != entry['name'] or pieces[1] != pin['version']:
        raise ValueError('Wheel filename identity mismatch')
    filename_tags = expanded_tag('-'.join(pieces[-3:]))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or any(n.startswith('/') or '\\' in n or ':' in n or '..' in PurePosixPath(n).parts for n in names):
            raise ValueError('Unsafe or duplicate wheel archive members')
        metadata_names = [n for n in names if n.endswith('.dist-info/METADATA')]
        if len(metadata_names) != 1:
            raise ValueError('Expected one wheel distribution metadata record')
        info = metadata_names[0]
        wheel_info = info[:-len('METADATA')] + 'WHEEL'
        if archive.getinfo(info).file_size > 2 * 1024 * 1024 or archive.getinfo(wheel_info).file_size > 2 * 1024 * 1024:
            raise ValueError('Oversized wheel metadata')
        metadata = BytesParser(policy=policy.default).parsebytes(archive.read(info))
        header = BytesParser(policy=policy.default).parsebytes(archive.read(wheel_info))
        if metadata.get_all('Name') != [metadata.get('Name')] or normalized(metadata.get('Name', '')) != entry['name'] or metadata.get('Version') != pin['version']:
            raise ValueError('Wheel embedded identity mismatch')
        header_tags = set().union(*(expanded_tag(t) for t in header.get_all('Tag', [])))
        if not filename_tags & header_tags & target_tags:
            raise ValueError('Wheel does not match target compatibility tags')


def wheel_manifest(data, locked):
    source = json.loads(data)
    packages = source.get('packages')
    if not isinstance(packages, dict) or set(packages) != set(locked) or source.get('errors') or not source.get('complete'):
        raise ValueError('Wheel manifest must exactly cover the locked dependency set')
    result = {}
    for name, item in packages.items():
        pin = locked[name]
        if item.get('name') != name or item.get('version') != pin['version'] or sorted(item.get('extras', [])) != pin['extras']:
            raise ValueError('Wheel/lock package identity or extras mismatch')
        link = urlsplit(item['source_url'])
        metadata = urlsplit(item['metadata_url'])
        if (link.scheme != 'https' or link.hostname != 'files.pythonhosted.org' or link.username or link.password or link.query or link.fragment
                or unquote(link.path.rsplit('/', 1)[-1]) != item['filename']
                or metadata.scheme != 'https' or metadata.hostname != 'pypi.org' or metadata.username or metadata.password or metadata.query or metadata.fragment
                or unquote(metadata.path) != '/pypi/' + name + '/' + pin['version'] + '/json'):
            raise ValueError('Wheel origins must be the reviewed official PyPI locations')
        result[name] = {k: item[k] for k in ('name', 'version', 'extras', 'filename', 'bytes', 'sha256', 'source_url', 'metadata_url', 'metadata_sha256')}
        if not HASH.fullmatch(result[name]['metadata_sha256']):
            raise ValueError('Invalid official metadata fingerprint')
    return {'complete': True, 'errors': {}, 'packages': dict(sorted(result.items()))}


def validate_payload(payload, app, proxy_ip, backend_ip, target):
    if target != {'python': '3.12.3', 'platform': 'linux', 'machine': 'x86_64'}:
        raise ValueError('Only the verified CPython 3.12.3 Linux x86_64 target is supported')
    lock = pins(payload['ops/requirements-linux.lock'], hashed=True)
    direct = pins(payload['application/backend/requirements.txt'])
    for name, pin in direct.items():
        if name not in lock or pin['version'] != lock[name]['version'] or not set(pin['extras']) <= set(lock[name]['extras']):
            raise ValueError('Linux lock does not cover direct requirements/extras')
    normal, _ = merge(payload['ops/nginx-before.conf'].decode('utf-8'), payload['application/deploy/nginx-travel.conf'].decode('utf-8'))
    if payload['ops/nginx-normal.conf'] != normal.encode('utf-8') or payload['ops/nginx-maintenance.conf'] != render_maintenance(normal)[0].encode('utf-8'):
        raise ValueError('Nginx candidates do not match the bound application template/source')
    unit = render_service(payload['ops/service-profile.conf.template'].decode('utf-8'), app['id'], proxy_ip, backend_ip)
    if payload['ops/travel.service'] != unit.encode('utf-8'):
        raise ValueError('Service unit is bound to another code/profile/network')
    tags = json.loads(payload['ops/target-tags.json'])
    if not isinstance(tags, list) or not tags or len(tags) > 5000 or any(not isinstance(t, str) for t in tags):
        raise ValueError('Invalid recorded target tag list')
    target_tags = set().union(*(expanded_tag(t) for t in tags))
    if not any(t.startswith('cp312-cp312-manylinux_') and t.endswith('_x86_64') for t in target_tags):
        raise ValueError('Target tags must include the verified Linux interpreter/architecture')
    wheels = wheel_manifest(payload['ops/wheels.json'], lock)
    if json.loads(payload['ops/wheels.json']) != wheels:
        raise ValueError('Unexpected wheel manifest fields')
    expected = {'application/' + n for n in app['files']} | {'application/release.json'}
    expected |= {'ops/' + name for name in OPS} | {'tools/' + name for name in HELPERS}
    for name, entry in wheels['packages'].items():
        member = 'wheelhouse/' + entry['filename']
        if member not in payload:
            raise ValueError('Missing wheel')
        verify_wheel(payload[member], entry, lock[name], target_tags)
        expected.add(member)
    if set(payload) != expected:
        raise ValueError('Unexpected plan members; secrets, data and unselected files are forbidden')
    for name in ('release_candidate.py', 'frontend_release.py', 'deployment_check.py', 'backup_db.py'):
        if payload['tools/' + name] != payload['application/backend/tools/' + name]:
            raise ValueError('Plan/application verification helpers differ')
    for name, data in payload.items():
        if name.endswith('.py'):
            ast.parse(data, filename=name)


def prepare(application, wheelhouse, wheel_index, lock_file, target_tags, nginx_source, service_profile, runbook, output, proxy_ip, backend_ip):
    application, wheelhouse, output = map(root_path, (application, wheelhouse, output))
    if output.exists():
        raise FileExistsError('Plan output must be new')
    inputs = [root_path(p) for p in (wheel_index, lock_file, target_tags, nginx_source, service_profile, runbook)]
    if any(output.is_relative_to(p) or p.is_relative_to(output) for p in (application, wheelhouse)) or any(p.is_relative_to(output) for p in inputs):
        raise ValueError('Plan output overlaps an input')
    app = verify_application(application)
    proxy_ip, backend_ip = private_ip(proxy_ip), private_ip(backend_ip)
    payload = {'application/' + name: child(application, name).read_bytes() for name in set(app['files']) | {'release.json'}}
    for name, path in zip(('ops/wheels.json', 'ops/requirements-linux.lock', 'ops/target-tags.json', 'ops/nginx-before.conf',
                           'ops/service-profile.conf.template', 'ops/RELEASE_RUNBOOK.md'), inputs):
        payload[name] = path.read_bytes()
    locked = pins(payload['ops/requirements-linux.lock'], hashed=True)
    index = wheel_manifest(payload['ops/wheels.json'], locked)
    payload['ops/wheels.json'] = json.dumps(index, sort_keys=True, indent=2).encode('utf-8')
    for item in index['packages'].values():
        payload['wheelhouse/' + item['filename']] = child(wheelhouse, item['filename']).read_bytes()
    normal, _ = merge(payload['ops/nginx-before.conf'].decode('utf-8'), payload['application/deploy/nginx-travel.conf'].decode('utf-8'))
    payload['ops/nginx-normal.conf'] = normal.encode('utf-8')
    payload['ops/nginx-maintenance.conf'] = render_maintenance(normal)[0].encode('utf-8')
    payload['ops/travel.service'] = render_service(payload['ops/service-profile.conf.template'].decode('utf-8'), app['id'], proxy_ip, backend_ip).encode('utf-8')
    for name in HELPERS:
        payload['tools/' + name] = child(root_path(Path(__file__).parent), name).read_bytes()
    target = {'python': '3.12.3', 'platform': 'linux', 'machine': 'x86_64'}
    validate_payload(payload, app, proxy_ip, backend_ip, target)
    body = {'format': 1, 'kind': 'travel-private-release-plan', 'target': target, 'application_id': app['id'],
            'frontend_id': app['frontend_id'], 'proxy_ip': proxy_ip, 'backend_bind_ip': backend_ip,
            'files': {n: digest(data) for n, data in payload.items()}}
    metadata = {**body, 'id': identifier(body)}
    output.mkdir(mode=0o700)
    for name, data in {**payload, 'plan.json': json.dumps(metadata, sort_keys=True, indent=2).encode('utf-8')}.items():
        path = child(output, name)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
    verify(output, expected_id=metadata['id'])
    return metadata


def verify(root, expected_id=None):
    root = root_path(root)
    metadata = json.loads(child(root, 'plan.json').read_text(encoding='utf-8'))
    if not isinstance(metadata, dict) or set(metadata) != KEYS:
        raise ValueError('Invalid plan manifest fields')
    body = {k: v for k, v in metadata.items() if k != 'id'}
    if body.get('format') != 1 or body.get('kind') != 'travel-private-release-plan' or metadata['id'] != identifier(body):
        raise ValueError('Invalid plan identity')
    if expected_id is not None and metadata['id'] != expected_id:
        raise ValueError('Plan differs from the separately reviewed identity')
    files = metadata['files']
    if not isinstance(files, dict) or not files or any(not isinstance(h, str) or not HASH.fullmatch(h) for h in files.values()):
        raise ValueError('Invalid plan checksum list')
    payload = {name: child(root, name).read_bytes() for name in files}
    if any(digest(payload[n]) != checksum for n, checksum in files.items()):
        raise ValueError('Plan checksum mismatch')
    actual = list(root.rglob('*'))
    wanted_files = set(files) | {'plan.json'}
    wanted_dirs = {p.as_posix() for name in wanted_files for p in PurePosixPath(name).parents if p.as_posix() != '.'}
    if ({p.relative_to(root).as_posix() for p in actual if p.is_file()} != wanted_files
            or {p.relative_to(root).as_posix() for p in actual if p.is_dir()} != wanted_dirs
            or any(p != p.resolve() for p in actual)):
        raise ValueError('Unexpected plan file, directory, symlink or junction')
    app = verify_application(child(root, 'application'))
    if app['id'] != metadata['application_id'] or app['frontend_id'] != metadata['frontend_id']:
        raise ValueError('Plan/application identity mismatch')
    validate_payload(payload, app, metadata['proxy_ip'], metadata['backend_bind_ip'], metadata['target'])
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    p = actions.add_parser('prepare')
    for name in ('application', 'wheelhouse', 'wheel-index', 'lock-file', 'target-tags', 'nginx-source', 'service-profile', 'runbook', 'output', 'proxy-ip', 'backend-ip'):
        p.add_argument('--' + name, required=True)
    p = actions.add_parser('verify'); p.add_argument('--plan', required=True); p.add_argument('--expected-id', required=True)
    args = parser.parse_args()
    try:
        if args.action == 'prepare':
            meta = prepare(**{k: v for k, v in vars(args).items() if k != 'action'})
        else:
            meta = verify(args.plan, expected_id=args.expected_id)
        print(json.dumps({'plan_id': meta['id'], 'application_id': meta['application_id'], 'files': len(meta['files']),
                          'plan_verified': True, 'production_ready': False, 'verification_scope': 'files and bindings only'}))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, SyntaxError, zipfile.BadZipFile) as error:
        parser.exit(1, 'Release plan refused: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
