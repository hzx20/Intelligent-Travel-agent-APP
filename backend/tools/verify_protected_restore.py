"""Rehearse the verified travel release in private RAM. Never publishes or exports rows."""
from contextlib import closing
import hashlib
from itertools import zip_longest
import json
import os
from pathlib import Path
import platform
import re
import resource
import secrets
import signal
import sqlite3
import stat
import sys
import tempfile
import time
from urllib.parse import urlsplit

RAM = Path('/dev/shm')
DATA = Path('/opt/travel/app/backend/data')
BASE = Path('/tmp/travel-release-rehearsal-20261116-3d8b3a0a')
STAGE = BASE / 'budget-38ba927c4ae4'
RUNTIME = BASE / 'runtime-env-20261117-review1/venv'
HARNESS_SHA = '177b79a770c23e1d222ddd51469d3d6356195626289020132153e664e00a1996'
LINUX_ID = '27592646387039b1e5d167264fa7fc1b31f672e3392167de0cf550dabeab45a6'
MIB = 1024 * 1024
PHASE = 'preflight'


def demand(value):
    if not value:
        raise RuntimeError('Check failed')


def file_metadata(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {'exists': False}
    kind = 'file' if stat.S_ISREG(info.st_mode) else 'directory' if stat.S_ISDIR(info.st_mode) else 'other'
    return {'exists': True, 'kind': kind, 'uid': info.st_uid, 'gid': info.st_gid,
            'mode': f'{stat.S_IMODE(info.st_mode):04o}', 'bytes': info.st_size if kind == 'file' else None,
            'canonical': path.resolve() == path, 'readable': os.access(path, os.R_OK)}


def free_bytes(path):
    space = os.statvfs(path)
    return space.f_bavail * space.f_frsize


def memory():
    mount = Path('/proc/self/mountinfo').read_text().splitlines()
    entries = [line.split(' - ', 1)[1].split() for line in mount if line.split()[4] == str(RAM)]
    top = entries[-1] if entries else []
    available = int(re.search(r'^MemAvailable:\s+(\d+) kB$', Path('/proc/meminfo').read_text(), re.M)[1]) * 1024
    return {'tmpfs': bool(top) and top[0] == 'tmpfs', 'mount_disables_swap': bool(top) and 'noswap' in top[2].split(','),
            'no_active_swap': len(Path('/proc/swaps').read_text().splitlines()) == 1,
            'ram_free_bytes': free_bytes(RAM), 'memory_available_bytes': available}


def inventory():
    names = ('app.db', 'app.db-wal', 'app.db-shm', 'app.db-journal', '.jwt_secret')
    artifacts = {n: file_metadata(DATA / n) for n in names}
    unknown = [p for p in DATA.iterdir() if p.name not in names]
    bytes_total = sum(artifacts[n].get('bytes') or 0 for n in names[:4])
    ram = memory()
    required = 16 * MIB + 8 * bytes_total
    eligible = (0 < bytes_total <= 16 * MIB and not unknown and artifacts['app.db'].get('kind') == 'file' and
                artifacts['app.db']['canonical'] and artifacts['app.db']['readable'] and
                ram['tmpfs'] and ram['mount_disables_swap'] and ram['ram_free_bytes'] >= required and
                ram['memory_available_bytes'] >= max(256 * MIB, required))
    return {'data_directory': file_metadata(DATA), 'sqlite_artifacts': artifacts, 'unknown_data_entries': len(unknown),
            'config_metadata': {str(p): file_metadata(p) for p in (
                DATA.parents[1] / '.env', Path('/etc/systemd/system/travel.service'),
                Path('/opt/continew/nginx.conf'), Path('/opt/continew/web/travel/index.html'))},
            'opt_available_bytes': free_bytes(DATA), 'ram': ram, 'rehearsal_required_bytes': required,
            'eligible_for_ram_rehearsal': eligible, 'database_content_read': False}


def open_read(path):
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)
    db.execute('PRAGMA query_only=ON')
    db.execute('PRAGMA temp_store=MEMORY')
    return closing(db)


def quote(value):
    return '"' + value.replace('"', '""') + '"'


def schema(db):
    names = [r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return {n: [r[1] for r in db.execute(f'PRAGMA table_info({quote(n)})')] for n in names}


def same_original_rows(snapshot, restored):
    sentinel = object()
    with open_read(snapshot) as src, open_read(restored) as dst:
        shape = schema(src)
        restored_shape = schema(dst)
        demand(bool(shape))
        for table, fields in shape.items():
            demand(bool(fields) and set(fields) <= set(restored_shape.get(table, [])))
            columns = ', '.join(f'{quote(col)} COLLATE BINARY' for col in fields)
            order = ', '.join(str(i) for i in range(1, len(fields) + 1))
            sql = f'SELECT {columns} FROM {quote(table)} ORDER BY {order}'
            for x, y in zip_longest(src.execute(sql), dst.execute(sql), fillvalue=sentinel):
                demand(x is not sentinel and y is not sentinel and x == y and tuple(map(type, x)) == tuple(map(type, y)))
        return len(shape)


def classify_images(path):
    result = {'external_urls': False, 'inline_data': False, 'local_or_unclassified_references': False}
    with open_read(path) as db:
        shape = schema(db)
        for table, col in (('guide_images', 'image_url'), ('users', 'avatar'), ('spots', 'image_url')):
            if col not in shape.get(table, []):
                continue
            for (value,) in db.execute(f'SELECT {quote(col)} FROM {quote(table)}'):
                if not value:
                    continue
                try:
                    parsed = urlsplit(value)
                    host = parsed.hostname
                    if parsed.scheme in ('http', 'https') and host and host not in ('134.175.137.18', 'localhost', '127.0.0.1', '::1'):
                        result['external_urls'] = True
                    elif parsed.scheme == 'data':
                        result['inline_data'] = True
                    else:
                        result['local_or_unclassified_references'] = True
                except (ValueError, TypeError, AttributeError):
                    result['local_or_unclassified_references'] = True
    return result


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def no_io_escape(event, _args):
    if event in {'socket.connect', 'socket.getaddrinfo', 'socket.gethostbyname', 'subprocess.Popen', 'os.system', 'os.exec'}:
        raise OSError('Outbound connection or new process denied')


def configure_synthetic_environment(restore_dir, work):
    conf = work / 'synthetic.env'
    conf.write_text('JWT_SECRET=' + secrets.token_urlsafe(48) + '\n', encoding='utf-8')
    os.environ.clear()
    os.environ.update(TRAVEL_ENV_FILE=str(conf), TRAVEL_DATA_DIR=str(restore_dir), TMPDIR=str(work),
                      TRAVEL_SHARED_LIMITS='0', TRAVEL_SHARED_CACHE='0', TRAVEL_PROVIDER_BUDGET='0')
    sys.addaudithook(no_io_escape)


def exercise(app, restore_dir):
    # The environment was isolated before importing any application modules.
    from app import main
    from app.db import database
    from fastapi.testclient import TestClient
    demand(Path(main.__file__).resolve() == app / 'backend/app/main.py' and database.DATA_DIR == restore_dir)
    database.engine.dispose()
    # Without the context manager TestClient never invokes lifespan. AnyIO's own
    # local self-pipe is permitted (it does not resolve or connect to a network).
    client = TestClient(main.app)
    try:
        demand(client.get('/api/health').json() == {'status': 'ok'} and client.get('/api/trips').status_code == 401)
    finally:
        client.close()
        database.engine.dispose()
    return database


def verify_rollback_source(src):
    # A `mode=ro` SQLite connection may still use WAL shared memory. Reject it
    # before opening a SQLite connection, and reject an existing rollback journal.
    actual = file_metadata(src)
    demand(actual.get('kind') == 'file' and actual['canonical'] and actual['readable'])
    with src.open('rb') as stream:
        header = stream.read(20)
        demand(header[:16] == b'SQLite format 3\x00' and header[18:20] == b'\x01\x01')
    demand(not any((src.parent / (src.name + suffix)).exists() for suffix in ('-journal', '-wal', '-shm')))
    with closing(sqlite3.connect(src.as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
        journal = db.execute('PRAGMA journal_mode').fetchone()[0]
    demand(journal in ('delete', 'truncate', 'persist'))
    return journal


def seed_photo(src):
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from app.db.footprint_models import FootprintPhoto, TripFootprint
    from app.db.trip_models import Trip
    engine = create_engine(f'sqlite:///{src.as_posix()}')
    try:
        with Session(engine) as session:
            trip_id, user_id = session.execute(select(Trip.id, Trip.user_id)).one()
            footprint = TripFootprint(user_id=user_id, trip_id=trip_id, day_number=1, place_name='Synthetic visit')
            footprint.photos.append(FootprintPhoto(data=b'\x00synthetic-original\xff'))
            session.add(footprint)
            session.commit()
    finally:
        engine.dispose()


def synthetic_negative_checks(snapshot, restored, work, backup_database, verify_database):
    damaged, wal = work / 'changed-test.db', work / 'wal-test.db'
    backup_database(restored, damaged, 8)
    with closing(sqlite3.connect(damaged)) as db:
        db.execute('UPDATE footprint_photos SET data = ?', (b'\x00synthetic-mutated\xff',))
        db.commit()
    verify_database(damaged, deadline=time.monotonic() + 8)
    try:
        same_original_rows(snapshot, damaged)
    except RuntimeError:
        pass
    else:
        demand(False)
    with closing(sqlite3.connect(wal)) as db:
        demand(db.execute('PRAGMA journal_mode = WAL').fetchone() == ('wal',))
        db.execute('CREATE TABLE test (id INTEGER PRIMARY KEY)')
        db.commit()
    try:
        verify_rollback_source(wal)
    except RuntimeError:
        return {'changed_photo_despite_sqlite_integrity_rejected': True, 'wal_header_rejected_before_sqlite_open': True}
    demand(False)


def execute(mode):
    global PHASE
    demand(mode in ('synthetic', 'production') and Path(sys.prefix) == RUNTIME and
           platform.python_version() == '3.12.3' and sys.flags.isolated and sys.dont_write_bytecode)
    for p in (BASE, STAGE):
        s = p.stat()
        demand(p.resolve() == p and s.st_uid == os.getuid() and stat.S_ISDIR(s.st_mode) and not s.st_mode & 0o077)
    harness = STAGE / 'rehearse_budget_runtime.py'
    demand(harness.resolve() == harness and sha(harness) == HARNESS_SHA)
    protections = memory()
    placement = RAM.stat()
    demand(protections['tmpfs'] and protections['mount_disables_swap'] and protections['ram_free_bytes'] >= 16*MIB and
           placement.st_uid == os.getuid() and stat.S_IMODE(placement.st_mode) == 0o700)
    meta = inventory() if mode == 'production' else None
    if meta:
        demand(meta['eligible_for_ram_rehearsal'])
    # tmpfs noswap covers file pages; also keep in-process SQLite rows resident.
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    demand(libc.prctl(4, 0, 0, 0, 0) == 0 and libc.prctl(3, 0, 0, 0, 0) == 0)  # PR_SET/GET_DUMPABLE
    _soft, hard = resource.getrlimit(resource.RLIMIT_MEMLOCK)
    resource.setrlimit(resource.RLIMIT_MEMLOCK, (hard, hard))
    demand(libc.mlockall(3) == 0)  # MCL_CURRENT | MCL_FUTURE
    sys.path.insert(0, str(STAGE))
    from rehearse_budget_runtime import read_archive, runtime_lock, seed, WINDOWS_ID, FRONTEND_ID
    demand(runtime_lock(STAGE / 'requirements-linux-cp312.lock', False) == 63)
    with tempfile.TemporaryDirectory(prefix='travel-budget-restore-', dir=RAM) as scratch:
        work = Path(scratch)
        print(json.dumps({'phase': 'started', 'private_ram_workdir': str(work)}), flush=True)
        source, app = work / 'source', work / 'candidate'
        read_archive(STAGE / 'application-source.tar.gz', source)
        sys.path.insert(0, str(source / 'backend'))
        from tools.release_candidate import prepare, verify, runtime_report
        old = verify(source)
        native = prepare(source, source / 'frontend', app)
        demand(old['id'] == WINDOWS_ID and native['frontend_id'] == FRONTEND_ID and native['id'] == LINUX_ID and
               native['files'] == old['files'] and runtime_report(app)['pinned_runtime_matches'])
        sys.path[0] = str(app / 'backend')
        from tools.backup_db import backup_database, verify_database
        snapshot = work / 'snapshot.db'
        restore_dir = work / 'restore'
        restore_dir.mkdir(mode=0o700)
        restored = restore_dir / 'app.db'
        configure_synthetic_environment(restore_dir, work)
        PHASE = 'snapshot'
        src = DATA / 'app.db'
        if not meta:
            PHASE = 'seed_synthetic_legacy'
            src = work / 'synthetic.db'
            # The release verifier already loaded deployment_check from source;
            # we verified that source and the native candidate have identical members.
            seed(source, src)
            seed_photo(src)
        PHASE = 'source_read_only_safety_check'
        journal = verify_rollback_source(src)
        PHASE = 'snapshot'
        start = time.monotonic()
        backup_database(src, snapshot, 12)
        snap_seconds = round(time.monotonic() - start, 3)
        snap_sha = sha(snapshot)
        snap_bytes = snapshot.stat().st_size
        demand(0 < snap_bytes <= 16*MIB and stat.S_IMODE(snapshot.stat().st_mode) == 0o600)
        PHASE = 'restore_and_migrate'
        start = time.monotonic()
        backup_database(snapshot, restored, 12)
        images = classify_images(snapshot)
        database = exercise(app, restore_dir)
        PHASE = 'verify_original_values'
        tables = same_original_rows(snapshot, restored)
        PHASE = 'verify_candidate_schema'
        from tools.deployment_check import check_schema
        demand(not check_schema(restored, timeout=8))
        migrated = sha(restored)
        PHASE = 'verify_idempotent_initialization'
        database.init_db()
        database.engine.dispose()
        demand(sha(restored) == migrated and sha(snapshot) == snap_sha and verify(app) == native)
        restore_seconds = round(time.monotonic() - start, 3)
        PHASE = 'synthetic_rejection_checks' if not meta else 'cleanup'
        faults = synthetic_negative_checks(snapshot, restored, work, backup_database, verify_database) if not meta else None
        PHASE = 'cleanup'
    demand(not work.exists())
    return {'phase': 'passed', 'scope': mode, 'candidate_id': LINUX_ID, 'original_tables_verified': tables,
            'original_values_preserved': True, 'second_init_unchanged': True, 'snapshot_unchanged': True,
            'app_health_and_private_gate_passed': True, 'apps_bound_to_network': False,
            'sqlite_journal_mode_observed': journal, 'snapshot_bytes': snap_bytes, 'timings_seconds': {'snapshot': snap_seconds,
            'restore_migrate_verify': restore_seconds}, 'media_reference_types': images,
            'ram_workdir_removed': True, 'ram_mount_disables_swap': True, 'no_credentials_or_rows_exported': True,
            'process_memory_locked': True, 'process_dump_disabled': True, 'candidate_schema_check_passed': True,
            'synthetic_negative_checks': faults, 'production_deployed': False}


def interrupt(_sig, _frame):
    raise TimeoutError('Rehearsal interrupted')


if __name__ == '__main__':
    try:
        os.umask(0o077)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        demand(os.getuid() != 0 and DATA.resolve() == DATA)
        for sig in (signal.SIGALRM, signal.SIGHUP, signal.SIGTERM):
            signal.signal(sig, interrupt)
        signal.alarm(65)
        mode = sys.argv[1]
        print(json.dumps(inventory() if mode == 'inventory' else execute(mode), sort_keys=True), flush=True)
    except BaseException as error:
        failure = {'phase': PHASE, 'passed': False, 'error_type': type(error).__name__}
        if sys.argv[1:2] == ['synthetic']:
            import traceback
            failure['synthetic_failure_sites'] = [f'{Path(f.filename).name}:{f.lineno}' for f in traceback.extract_tb(error.__traceback__)]
        print(json.dumps(failure), flush=True)
        raise SystemExit(1) from None
