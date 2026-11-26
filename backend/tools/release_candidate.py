"""Assemble and verify a code-only backend/frontend candidate; never deploy it."""
import argparse
import ast
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.frontend_release import child, digest, identifier, root_path, verify as verify_frontend

TOOLS = {'backup_db.py', 'deployment_check.py', 'frontend_release.py', 'release_candidate.py'}
FIXED = {'backend/requirements.txt', 'backend/scripts/fetch_pois.py',
         'deploy/nginx-travel.conf', 'deploy/travel.service', 'deploy/README.md'}
REQUIRED = FIXED | {'backend/tools/' + name for name in TOOLS} | {
    'backend/app/main.py', 'backend/app/db/database.py', 'backend/app/core/config.py',
    'backend/app/security.py', 'frontend/release.json', 'frontend/index.html',
    'frontend/.vite/manifest.json'}


def allowed(name):
    if not isinstance(name, str):
        return False
    parts = name.split('/')
    if any(p in ('', '.', '..', '__pycache__') for p in parts):
        return False
    if name in FIXED or name in {'backend/tools/' + n for n in TOOLS}:
        return True
    if name.startswith('backend/app/') and name.endswith('.py'):
        return not any(p.startswith('.') for p in parts)
    if name in ('frontend/release.json', 'frontend/index.html', 'frontend/.vite/manifest.json'):
        return True
    if name.startswith('frontend/assets/'):
        return not any(p.startswith('.') for p in parts) and not name.lower().endswith(('.db', '.sqlite', '.sqlite3', '.pem', '.key'))
    return False


def check_python(name, data):
    if name.endswith('.py'):
        ast.parse(data, filename=name)  # Parse only: no import, DB initialization or pyc.


def parse_requirements(data):
    packages = {}
    for raw in data.decode('utf-8-sig').splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[A-Za-z0-9_,.-]+\])?==([A-Za-z0-9_.+!-]+)', line)
        if not match:
            raise ValueError('Requirements must contain only exact version pins')
        name, version = match.groups()
        normalized = re.sub(r'[-_.]+', '-', name).lower()
        if normalized in packages:
            raise ValueError('Duplicate pinned dependency')
        packages[normalized] = version
    if not packages:
        raise ValueError('No pinned dependencies')
    return packages


def prepare(source, frontend, output):
    source, frontend, output = map(root_path, (source, frontend, output))
    # An ignored sibling artifact directory under the repository is supported.
    # Never place output inside any collected tree or inside its frontend input.
    if source.is_relative_to(output) or any(output.is_relative_to(source / n) for n in ('backend', 'deploy')) or output.is_relative_to(frontend) or frontend.is_relative_to(output):
        raise ValueError('Candidate output overlaps an input tree')
    front = verify_frontend(frontend)
    names = FIXED | {'backend/tools/' + name for name in TOOLS}
    names |= {p.relative_to(source).as_posix() for p in child(source, 'backend/app').rglob('*.py')
              if '__pycache__' not in p.parts and not any(part.startswith('.') for part in p.relative_to(source).parts)}
    payload = {name: child(source, name).read_bytes() for name in names}
    for name in set(front['files']) | {'release.json'}:
        payload['frontend/' + name] = child(frontend, name).read_bytes()
    if not REQUIRED <= payload.keys() or not all(allowed(name) for name in payload):
        raise ValueError('Incomplete or unsupported candidate files')
    for name, data in payload.items():
        check_python(name, data)
    parse_requirements(payload['backend/requirements.txt'])
    body = {'format': 1, 'kind': 'travel-release-candidate', 'frontend_id': front['id'],
            'prepared_python': platform.python_version(), 'files': {n: digest(data) for n, data in payload.items()}}
    metadata = {**body, 'id': identifier(body)}
    output.mkdir(mode=0o700)  # Never overwrite a previous candidate.
    for name, data in payload.items():
        destination = child(output, name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    (output / 'release.json').write_text(json.dumps(metadata, sort_keys=True, indent=2), encoding='utf-8')
    verify(output)
    return metadata


def verify(candidate):
    candidate = root_path(candidate)
    metadata = json.loads(child(candidate, 'release.json').read_text(encoding='utf-8'))
    if not isinstance(metadata, dict):
        raise ValueError('Invalid release manifest')
    body = {k: v for k, v in metadata.items() if k != 'id'}
    if body.get('format') != 1 or body.get('kind') != 'travel-release-candidate' or metadata.get('id') != identifier(body):
        raise ValueError('Invalid release identity')
    files = metadata.get('files')
    if not isinstance(files, dict) or not REQUIRED <= files.keys() or not re.fullmatch(r'\d+\.\d+\.\d+', metadata.get('prepared_python', '')):
        raise ValueError('Incomplete release manifest')
    for name, expected in files.items():
        if not allowed(name) or not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
            raise ValueError('Unsupported release member')
        data = child(candidate, name).read_bytes()
        if digest(data) != expected:
            raise ValueError('Release checksum mismatch')
        check_python(name, data)
    actual = {p.relative_to(candidate).as_posix() for p in candidate.rglob('*') if p.is_file()}
    if actual != set(files) | {'release.json'} or any(p.is_symlink() for p in candidate.rglob('*')):
        raise ValueError('Unexpected release file or symlink')
    parse_requirements(child(candidate, 'backend/requirements.txt').read_bytes())
    if verify_frontend(child(candidate, 'frontend'))['id'] != metadata['frontend_id']:
        raise ValueError('Frontend release identity mismatch')
    return metadata


def runtime_report(candidate):
    candidate = root_path(candidate)
    metadata = verify(candidate)
    observed, errors = {}, []
    for name, required in parse_requirements(child(candidate, 'backend/requirements.txt').read_bytes()).items():
        try:
            observed[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            observed[name] = None
        if observed[name] != required:
            errors.append('Missing or mismatched pinned dependency: ' + name)
    same_python = platform.python_version_tuple()[:2] == tuple(metadata['prepared_python'].split('.')[:2])
    if not same_python:
        errors.append('Python major/minor differs from candidate preparation environment')
    return {'release_id': metadata['id'], 'python': platform.python_version(), 'packages': observed,
            'pinned_runtime_matches': not errors, 'errors': errors,
            'not_checked': ['transitive dependencies and optional extras', 'Linux native libraries and platform behavior',
                            'production settings, data migration and external providers', 'independent startup and release approval']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    p = commands.add_parser('prepare')
    p.add_argument('--source', required=True)
    p.add_argument('--frontend', required=True)
    p.add_argument('--output', required=True)
    for action in ('verify', 'runtime'):
        p = commands.add_parser(action)
        p.add_argument('--candidate', required=True)
    args = parser.parse_args()
    try:
        if args.action == 'prepare':
            result = prepare(args.source, args.frontend, args.output)
            print(json.dumps({'release_id': result['id'], 'frontend_id': result['frontend_id'], 'files': len(result['files'])}))
        elif args.action == 'verify':
            result = verify(args.candidate)
            print(json.dumps({'release_id': result['id'], 'candidate_verified': True}))
        else:
            report = runtime_report(args.candidate)
            print(json.dumps(report, indent=2))
            return 0 if report['pinned_runtime_matches'] else 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError, SyntaxError) as error:
        parser.exit(1, f'Candidate operation failed: {error}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
