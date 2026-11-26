"""Prepare, publish and roll back verified frontend candidates; never delete assets."""
import argparse
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.deployment_check import check_frontend

CONTROL = '.travel-deploy'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def root_path(path):
    path = Path(path).absolute()
    if path != path.resolve():
        raise ValueError('Symlink, junction or noncanonical directory not permitted')
    return path


def child(root, name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        raise ValueError('Unsafe release path')
    parts = PurePosixPath(name).parts
    if name.startswith('/') or any(p in ('.', '..') for p in name.split('/')):
        raise ValueError('Unsafe release path')
    path = root.joinpath(*parts)
    if path != path.resolve() or not path.resolve().is_relative_to(root):
        raise ValueError('Symlink or unsafe release path')
    return path


def public_directory(path):
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o755, exist_ok=True)
        directory.chmod(0o755)


def atomic(path, data, *, public=False):
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('xb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temp.chmod(0o644 if public else 0o600)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def identifier(files):
    return digest(json.dumps(files, sort_keys=True, separators=(',', ':')).encode())


def member(name):
    return isinstance(name, str) and (name in ('index.html', '.vite/manifest.json') or name.startswith('assets/'))


def prepare(source, output):
    source = root_path(source)
    output = root_path(output)
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('Candidate must be separate from source')
    if check_frontend(source):
        raise ValueError('Frontend build validation failed')
    manifest = json.loads(child(source, '.vite/manifest.json').read_text(encoding='utf-8'))
    names = {'index.html', '.vite/manifest.json'}
    for item in manifest.values():
        names.update([item['file'], *item.get('css', []), *item.get('assets', [])])
    if not all(member(n) for n in names):
        raise ValueError('Unsupported public asset location')
    payload = {n: child(source, n).read_bytes() for n in names}
    files = {n: digest(data) for n, data in payload.items()}
    metadata = {'format': 1, 'id': identifier(files), 'files': files}
    output.mkdir(mode=0o700)  # Never replace an existing candidate.
    for name, data in payload.items():
        target = child(output, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (output / 'release.json').write_text(json.dumps(metadata, sort_keys=True, indent=2), encoding='utf-8')
    verify(output)
    return metadata


def verify(candidate):
    candidate = root_path(candidate)
    metadata = json.loads(child(candidate, 'release.json').read_text(encoding='utf-8'))
    if not isinstance(metadata, dict):
        raise ValueError('Invalid candidate manifest')
    files = metadata.get('files')
    if metadata.get('format') != 1 or not isinstance(files, dict) or metadata.get('id') != identifier(files):
        raise ValueError('Invalid candidate manifest')
    if not {'index.html', '.vite/manifest.json'} <= set(files):
        raise ValueError('Missing release entry')
    for name, expected in files.items():
        if not member(name) or not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
            raise ValueError('Invalid candidate member')
        if digest(child(candidate, name).read_bytes()) != expected:
            raise ValueError('Candidate checksum mismatch')
    actual = {p.relative_to(candidate).as_posix() for p in candidate.rglob('*') if p.is_file()}
    if actual != set(files) | {'release.json'}:
        raise ValueError('Unexpected candidate file')
    if any(p.is_symlink() for p in candidate.rglob('*')) or check_frontend(candidate):
        raise ValueError('Candidate build validation failed')
    return metadata


def read_optional(path):
    return path.read_bytes() if path.exists() else None


def hash_optional(data):
    return digest(data) if data is not None else None


def encode(data):
    return base64.b64encode(data).decode() if data is not None else None


def decode(data):
    return base64.b64decode(data, validate=True) if data is not None else None


@contextmanager
def locked(target):
    target = root_path(target)
    if target == Path(target.anchor):
        raise ValueError('Unsafe publish target')
    public_directory(target)
    control = child(target, CONTROL)
    if not control.exists():
        control.mkdir(mode=0o700, exist_ok=True)
        control.chmod(0o700)
    lock = child(target, CONTROL + '/publish.lock')
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        fd = None
        yield target, control
    finally:
        if fd is not None:
            os.close(fd)
        lock.unlink(missing_ok=True)


def immutable_asset(data, target, expected):
    if digest(data) != expected:
        raise ValueError('Candidate changed during publication')
    if target.exists():
        if digest(target.read_bytes()) != expected:
            raise ValueError('Existing asset differs; immutable filename collision')
        return
    public_directory(target.parent)
    temp = target.with_name('.' + target.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('xb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temp.chmod(0o644)
        try:
            os.link(temp, target)  # Same-volume atomic create, never overwrite.
        except FileExistsError:
            if digest(target.read_bytes()) != expected:
                raise ValueError('Concurrent asset differs')
    finally:
        temp.unlink(missing_ok=True)


def load_state(record, release_id):
    state = json.loads(record.read_text(encoding='utf-8'))
    if state.get('id') != release_id or state.get('phase') not in ('prepared', 'committed', 'rolled_back'):
        raise ValueError('Invalid release state')
    for key in ('published_entry', 'published_manifest'):
        if not re.fullmatch('[0-9a-f]{64}', state[key]):
            raise ValueError('Invalid release state hash')
    decode(state['previous_entry'])
    decode(state['previous_manifest'])
    return state


def save_state(record, state):
    atomic(record, json.dumps(state, sort_keys=True).encode())


def restore(path, data):
    if data is None:
        path.unlink(missing_ok=True)
    else:
        atomic(path, data, public=True)


def publish(candidate, target):
    candidate = root_path(candidate)
    metadata = verify(candidate)
    # Read and recheck once before touching the target, preventing mixed candidates.
    payload = {n: child(candidate, n).read_bytes() for n in metadata['files']}
    if any(digest(data) != metadata['files'][n] for n, data in payload.items()):
        raise ValueError('Candidate changed during publication')
    target = root_path(target)
    if candidate.is_relative_to(target) or target.is_relative_to(candidate):
        raise ValueError('Source and target must be separate')
    with locked(target) as (target, control):
        entry = child(target, 'index.html')
        manifest = child(target, '.vite/manifest.json')
        record = child(control, metadata['id'] + '.json')
        if record.exists():
            state = load_state(record, metadata['id'])
            if state['phase'] == 'rolled_back':
                raise ValueError('Rolled-back release will not be reactivated')
            if state['published_entry'] != metadata['files']['index.html'] or state['published_manifest'] != metadata['files']['.vite/manifest.json']:
                raise ValueError('Candidate differs from recorded publication')
        else:
            state = {'id': metadata['id'], 'phase': 'prepared', 'previous_entry': encode(read_optional(entry)),
                     'previous_manifest': encode(read_optional(manifest)), 'published_entry': metadata['files']['index.html'],
                     'published_manifest': metadata['files']['.vite/manifest.json']}
            save_state(record, state)
        old_entry, old_manifest = decode(state['previous_entry']), decode(state['previous_manifest'])
        current = read_optional(entry)
        if hash_optional(current) == state['published_entry']:
            for name, expected in metadata['files'].items():
                if digest(child(target, name).read_bytes()) != expected:
                    raise ValueError('Published release drifted')
            state['phase'] = 'committed'
            save_state(record, state)
            return state['id']
        if state['phase'] == 'committed' or current != old_entry:
            raise ValueError('Current entry changed; refusing stale publication')
        if hash_optional(read_optional(manifest)) not in (hash_optional(old_manifest), state['published_manifest']):
            raise ValueError('Current metadata changed; refusing stale publication')
        for name, expected in metadata['files'].items():
            if name.startswith('assets/'):
                immutable_asset(payload[name], child(target, name), expected)
        public_directory(manifest.parent)
        try:
            atomic(manifest, payload['.vite/manifest.json'], public=True)
            if read_optional(entry) != old_entry:
                raise ValueError('Entry changed during preparation')
            atomic(entry, payload['index.html'], public=True)  # Visible switch is last.
            state['phase'] = 'committed'
            save_state(record, state)
        except Exception:
            if read_optional(entry) == old_entry and hash_optional(read_optional(manifest)) == state['published_manifest']:
                restore(manifest, old_manifest)
            raise
        return state['id']


def rollback(target, release_id):
    if not re.fullmatch('[0-9a-f]{64}', release_id):
        raise ValueError('Invalid release ID')
    with locked(target) as (target, control):
        record = child(control, release_id + '.json')
        state = load_state(record, release_id)
        entry = child(target, 'index.html')
        manifest = child(target, '.vite/manifest.json')
        old, old_manifest = decode(state['previous_entry']), decode(state['previous_manifest'])
        current, current_manifest = read_optional(entry), read_optional(manifest)
        if state['phase'] == 'rolled_back':
            return release_id  # A replay never overwrites a newer release.
        if hash_optional(current) not in (hash_optional(old), state['published_entry']) or hash_optional(current_manifest) not in (hash_optional(old_manifest), state['published_manifest']):
            raise ValueError('Current release differs; refusing stale rollback')
        # Also completes an interrupted rollback or aborts a prepared publication.
        restore(manifest, old_manifest)
        restore(entry, old)
        state['phase'] = 'rolled_back'
        save_state(record, state)
        return release_id


def status(target, release_id):
    if not re.fullmatch('[0-9a-f]{64}', release_id):
        raise ValueError('Invalid release ID')
    target = root_path(target)
    state = load_state(child(target, CONTROL + '/' + release_id + '.json'), release_id)
    entry = hash_optional(read_optional(child(target, 'index.html')))
    manifest = hash_optional(read_optional(child(target, '.vite/manifest.json')))
    return {'id': release_id, 'phase': state['phase'], 'matches_published': entry == state['published_entry'] and manifest == state['published_manifest'],
            'matches_previous': entry == hash_optional(decode(state['previous_entry'])) and manifest == hash_optional(decode(state['previous_manifest']))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('prepare'); p.add_argument('--source', required=True); p.add_argument('--output', required=True)
    p = sub.add_parser('verify'); p.add_argument('--candidate', required=True)
    p = sub.add_parser('publish'); p.add_argument('--candidate', required=True); p.add_argument('--target', required=True)
    for action in ('rollback', 'status'):
        p = sub.add_parser(action); p.add_argument('--target', required=True); p.add_argument('--release-id', required=True)
    args = parser.parse_args()
    try:
        if args.action == 'prepare': result = prepare(args.source, args.output)['id']
        elif args.action == 'verify': result = verify(args.candidate)['id']
        elif args.action == 'publish': result = publish(args.candidate, args.target)
        elif args.action == 'rollback': result = rollback(args.target, args.release_id)
        else: result = status(args.target, args.release_id)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        parser.exit(1, f'Release operation failed: {error}\n')
    print(json.dumps({'action': args.action, 'result': result}))


if __name__ == '__main__':
    main()
