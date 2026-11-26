"""Run a trusted candidate on a private restored copy; remove business copies afterwards.

Uses the current interpreter/dependencies. Does not install, publish, restart a
system service, copy production secrets, or retain business rows in its report.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def fingerprints(path, columns=None):
    """Hash ordered values internally; never include business rows in a report."""
    result = {}
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for table in (columns if columns is not None else tables):
            names = columns[table]['columns'] if columns is not None else [r[1] for r in db.execute('PRAGMA table_info(' + quote(table) + ')')]
            fields = ','.join(map(quote, names))
            digest = hashlib.sha256()
            count = 0
            for row in db.execute('SELECT ' + fields + ' FROM ' + quote(table) + ' ORDER BY ' + fields):
                encoded = repr(tuple(row)).encode('utf-8')
                digest.update(len(encoded).to_bytes(8, 'big'))
                digest.update(encoded)
                count += 1
            result[table] = {'columns': names, 'count': count, 'sha256': digest.hexdigest()}
    return result


def require_empty_jobs(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for name in ('trip_ai_jobs', 'plan_generation_jobs'):
            if name in tables and db.execute('SELECT COUNT(*) FROM ' + quote(name)).fetchone()[0]:
                raise ValueError('Rehearsal refuses a snapshot containing persistent job rows')


def validate_paths(candidate, source, work):
    candidate, source, work = (Path(p).absolute() for p in (candidate, source, work))
    if any(p != p.resolve() for p in (candidate, source, work)):
        raise ValueError('Use canonical paths without symlinks or junctions')
    if work.is_relative_to(candidate) or candidate.is_relative_to(work) or source.is_relative_to(work):
        raise ValueError('Work directory overlaps source or candidate')
    if not source.is_file():
        raise ValueError('Source database must already exist')
    if work.exists():
        raise FileExistsError('Work directory must be new')
    return candidate, source, work


def request(port, route):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f'http://127.0.0.1:{port}' + route, timeout=2) as response:
            return response.status, response.read(1024)
    except urllib.error.HTTPError as error:
        return error.code, error.read(1024)


def serve_once(candidate, data, log_path, env):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    bootstrap = r'''import pathlib,socket,sys
root=pathlib.Path(sys.argv[1]).resolve()
original=socket.socket.connect
def local_only(sock,address):
    if sock.family in (socket.AF_INET,socket.AF_INET6) and address[0] not in ('127.0.0.1','::1'):
        raise RuntimeError('External connections forbidden in rehearsal')
    return original(sock,address)
socket.socket.connect=local_only
import app.main as entry
from app.db.database import DATA_DIR
assert pathlib.Path(entry.__file__).resolve()==root/'backend/app/main.py'
assert DATA_DIR==pathlib.Path(sys.argv[2]).resolve()
import uvicorn
uvicorn.run(entry.app,host='127.0.0.1',port=int(sys.argv[3]),log_level='warning',proxy_headers=False)
'''
    with log_path.open('ab') as log:
        process = subprocess.Popen([sys.executable, '-B', '-c', bootstrap, str(candidate), str(data), str(port)],
                                   cwd=candidate / 'backend', env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('Candidate exited during startup; inspect private server.log')
                try:
                    status, body = request(port, '/api/health')
                    if status == 200 and json.loads(body) == {'status': 'ok'}:
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(.1)
            else:
                raise TimeoutError('Candidate health check timed out')
            if request(port, '/api/trips')[0] != 401:
                raise RuntimeError('Unauthenticated trip list did not reject access')
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def rehearse(candidate, source, work):
    candidate, source, work = validate_paths(candidate, source, work)
    sys.path.insert(0, str(candidate / 'backend'))
    from tools.release_candidate import verify, runtime_report
    from tools.backup_db import backup_database
    from tools.deployment_check import check_schema, check_frontend
    metadata = verify(candidate)
    runtime = runtime_report(candidate)
    if not runtime['pinned_runtime_matches']:
        raise ValueError('Candidate runtime comparison failed')
    work.mkdir(mode=0o700)
    work.chmod(0o700)
    data = work / 'data'
    data.mkdir(mode=0o700)
    snapshot = work / 'snapshot.db'
    source_stat = source.stat()
    report = {'release_id': metadata['id'], 'python': sys.version.split()[0], 'platform': sys.platform,
              'source_read_only': True, 'production_deployed': False, 'success': False}
    old_data_dir = os.environ.get('TRAVEL_DATA_DIR')
    try:
        backup_database(source, snapshot)
        require_empty_jobs(snapshot)
        backup_database(snapshot, data / 'app.db')
        before = fingerprints(snapshot)
        snapshot_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
               'TRAVEL_DATA_DIR': str(data), 'JWT_SECRET': secrets.token_hex(32),
               'ZHIPU_API_KEY': '', 'AMAP_API_KEY': '', 'AMAP_JS_KEY': '', 'AMAP_JS_SECURITY_CODE': '',
               'TRAVEL_SHARED_LIMITS': '1', 'TRAVEL_SHARED_CACHE': '1', 'TRAVEL_PROVIDER_BUDGET': '1'}
        # Retain a caller-supplied isolated dependency overlay, never workspace code.
        os.environ['TRAVEL_DATA_DIR'] = str(data)
        serve_once(candidate, data, work / 'server.log', env)
        after = fingerprints(data / 'app.db', before)
        if before != after:
            raise RuntimeError('Original columns or rows changed in the migrated copy')
        errors = check_schema(data / 'app.db') + check_frontend(candidate / 'frontend')
        if errors:
            report['preflight_errors'] = errors
            raise RuntimeError('Migrated copy did not pass schema/frontend preflight')
        first = fingerprints(data / 'app.db')
        serve_once(candidate, data, work / 'server.log', env)
        if fingerprints(data / 'app.db') != first:
            raise RuntimeError('Second startup changed the database contents')
        if hashlib.sha256(snapshot.read_bytes()).hexdigest() != snapshot_hash or verify(candidate) != metadata:
            raise RuntimeError('Snapshot or candidate changed during rehearsal')
        report.update({'success': True, 'startup_count': 2, 'original_tables_preserved': {name: {'rows': item['count'], 'columns': len(item['columns'])} for name, item in before.items()},
                       'original_values_preserved': True, 'second_startup_unchanged': True,
                       'schema_and_frontend_preflight_passed': True, 'candidate_and_snapshot_unchanged': True})
    except Exception as error:
        report['error'] = type(error).__name__ + ': ' + str(error)[:300]
    finally:
        if old_data_dir is None:
            os.environ.pop('TRAVEL_DATA_DIR', None)
        else:
            os.environ['TRAVEL_DATA_DIR'] = old_data_dir
        # Only this run's exclusively created paths; source and candidate are outside.
        if data.resolve().parent != work or snapshot.resolve().parent != work:
            raise ValueError('Refusing cleanup outside private work directory')
        shutil.rmtree(data)
        for suffix in ('', '-wal', '-shm', '-journal'):
            snapshot.with_name(snapshot.name + suffix).unlink(missing_ok=True)
        report['business_copies_removed'] = True
        final_stat = source.stat()
        report['source_file_observed_unchanged'] = (source_stat.st_ino, source_stat.st_size, source_stat.st_mtime_ns) == (final_stat.st_ino, final_stat.st_size, final_stat.st_mtime_ns)
        path = work / 'report.json'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--workdir', required=True)
    args = parser.parse_args()
    try:
        report = rehearse(args.candidate, args.source, args.workdir)
    except (OSError, ValueError) as error:
        parser.exit(1, f'Rehearsal refused: {error}\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
