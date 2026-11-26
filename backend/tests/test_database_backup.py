import sqlite3
import pytest
from tools.backup_db import backup_database, verify_database


def test_snapshot_includes_committed_wal_and_private_blobs_and_can_restore(tmp_path):
    source, backup, restored = [tmp_path / name for name in ('app.db','backup.db','restored.db')]
    with sqlite3.connect(source) as live:
        assert live.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
        live.execute('CREATE TABLE memories (id INTEGER PRIMARY KEY, note TEXT, photo BLOB)')
        live.execute('INSERT INTO memories VALUES (1, ?, ?)', ('旅行记录', b'private-photo-bytes'))
        live.commit()
        live.execute('BEGIN')
        live.execute('INSERT INTO memories VALUES (2, ?, ?)', ('尚未提交', b'not-committed'))
        backup_database(source, backup)
        live.rollback()
        # Rehearsal writes a new DB and leaves the live source untouched.
        backup_database(backup, restored)
        for path in (source, backup, restored):
            verify_database(path)
            with sqlite3.connect(path) as db:
                assert db.execute('SELECT * FROM memories').fetchall() == [(1, '旅行记录', b'private-photo-bytes')]


def test_backup_refuses_overwrite_missing_source_and_same_target(tmp_path):
    source, target = tmp_path/'source.db', tmp_path/'existing.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE sample (id INTEGER)')
    target.write_bytes(b'keep existing backup')
    with pytest.raises(FileExistsError): backup_database(source, target)
    assert target.read_bytes() == b'keep existing backup'
    with pytest.raises(ValueError): backup_database(source, source)
    with pytest.raises(sqlite3.OperationalError): backup_database(tmp_path/'missing.db', tmp_path/'new.db')
    assert not (tmp_path/'new.db').exists()


def test_invalid_source_does_not_leave_a_completed_backup(tmp_path):
    source, target = tmp_path/'broken.db', tmp_path/'backup.db'
    source.write_bytes(b'not a SQLite database')
    with pytest.raises(sqlite3.DatabaseError): backup_database(source, target)
    assert source.read_bytes() == b'not a SQLite database'
    assert not target.exists()


def test_timeout_removes_incomplete_output(tmp_path, monkeypatch):
    from tools import backup_db
    source, target = tmp_path/'source.db', tmp_path/'backup.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE sample (id INTEGER)')
    ticks = iter((0, 2))
    monkeypatch.setattr(backup_db.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(TimeoutError): backup_database(source, target, timeout_seconds=1)
    assert not target.exists()


def test_application_trip_reservation_and_photo_survive_restore(client, tmp_path):
    from test_trips import account
    from test_footprint_photos import photo_bytes
    c, sessions = client
    owner = account(c, 'backup_owner')
    trip = c.post('/api/trips', headers=owner, json={'title':'备份验收', 'days':1}).json()
    trip = c.post(f"/api/trips/{trip['id']}/days/{trip['days'][0]['id']}/items", headers=owner,
                  json={'version':trip['version'], 'name':'西湖'}).json()
    item = trip['days'][0]['items'][0]['id']
    trip = c.post(f"/api/trips/{trip['id']}/reservations", headers=owner,
                  json={'version':trip['version'], 'kind':'住宿', 'title':'测试酒店', 'status':'已预订', 'reference':'TEST-ORDER', 'item_id':item}).json()
    trip = c.post(f"/api/trips/{trip['id']}/footprints", headers=owner,
                  json={'version':trip['version'], 'item_id':item}).json()
    record = c.get(f"/api/trips/{trip['id']}/footprints", headers=owner).json()['items'][0]
    response = c.post(f"/api/trips/{trip['id']}/footprints/{record['id']}/photos", content=photo_bytes(),
                      headers={**owner,'Content-Type':'image/jpeg','X-Trip-Version':str(trip['version'])})
    assert response.status_code == 201
    source = sessions.kw['bind'].url.database
    backup, restored = tmp_path/'snapshot.db', tmp_path/'restored.db'
    backup_database(source, backup); backup_database(backup, restored)
    live, recovered = sqlite3.connect(source), sqlite3.connect(restored)
    try:
        for table in ('users','trips','trip_days','trip_items','trip_reservations','trip_footprints','footprint_photos'):
            original = live.execute(f'SELECT * FROM {table} ORDER BY id').fetchall()
            assert original
            assert recovered.execute(f'SELECT * FROM {table} ORDER BY id').fetchall() == original
    finally:
        live.close(); recovered.close()


def test_orphaned_rows_fail_verification_and_do_not_leave_snapshot(tmp_path):
    source, target = tmp_path/'orphan.db', tmp_path/'snapshot.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE parents(id INTEGER PRIMARY KEY)')
        db.execute('CREATE TABLE children(id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parents(id))')
        db.execute('INSERT INTO children VALUES (1, 999)')
        db.commit()
        assert db.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
    before = source.read_bytes()
    with pytest.raises(ValueError, match='foreign key'):
        verify_database(source)
    with pytest.raises(ValueError, match='foreign key'):
        backup_database(source, target)
    assert not target.exists() and source.read_bytes() == before


def test_verification_deadline_is_part_of_backup_cleanup(tmp_path, monkeypatch):
    from tools import backup_db
    source, target = tmp_path/'source.db', tmp_path/'snapshot.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE sample(id INTEGER)')
    original = backup_db.verify_database
    observed = []
    def expired_verification(path, *, deadline=None):
        observed.append(deadline)
        return original(path, deadline=0)
    monkeypatch.setattr(backup_db, 'verify_database', expired_verification)
    with pytest.raises(TimeoutError, match='verification timed out'):
        backup_database(source, target)
    assert observed[0] is not None and not target.exists()


def test_verify_only_cli_is_read_only_and_rejects_orphans(tmp_path):
    from pathlib import Path
    import subprocess
    import sys
    source = tmp_path/'source.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE parent(id INTEGER PRIMARY KEY)')
        db.execute('CREATE TABLE child(id INTEGER REFERENCES parent(id))')
    command = [sys.executable, str(Path(__file__).parents[1]/'tools'/'backup_db.py'), '--source', str(source), '--verify-only']
    before = source.read_bytes()
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and 'read-only' in result.stdout
    assert source.read_bytes() == before
    with sqlite3.connect(source) as db:
        db.execute('INSERT INTO child VALUES(9)')
        db.commit()
    before = source.read_bytes()
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1 and 'foreign key' in result.stderr
    assert source.read_bytes() == before


def test_verification_interrupts_sql_work_at_deadline(tmp_path, monkeypatch):
    from tools import backup_db
    source = tmp_path/'large.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE sample(id INTEGER PRIMARY KEY, note TEXT)')
        db.executemany('INSERT INTO sample VALUES (?, ?)', ((i, 'sample') for i in range(2000)))
        db.commit()
    before = source.read_bytes()
    ticks = iter((0, 2))
    monkeypatch.setattr(backup_db.time, 'monotonic', lambda: next(ticks, 2))
    with pytest.raises(TimeoutError, match='verification timed out'):
        verify_database(source, deadline=1)
    assert source.read_bytes() == before
