import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile

import pytest

from tools.backup_db import backup_database
from tools import collect_travel_backup as collector
from tools import seal_travel_backup as recovery


def prepared(tmp_path):
    folder = tmp_path/'prepared'
    for name in recovery.REQUIRED - {'data/app.db'}:
        path = folder/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('Synthetic recovery member '+name,encoding='utf-8')
    source = tmp_path/'fixture.sqlite3'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE example (msg TEXT NOT NULL, photo BLOB NOT NULL)')
        db.execute('INSERT INTO example VALUES (?, ?)', ('synthetic travel row', b'synthetic-photo'))
    target = folder/'data/app.db'
    target.parent.mkdir()
    backup_database(source,target)
    return folder


def test_missing_or_reused_paths_are_refused_before_encryption(tmp_path):
    source = prepared(tmp_path)
    keyring = tmp_path/'keyring'
    keyring.mkdir()
    archive = tmp_path/'backup.gpg'
    archive.write_bytes(b'keep this')
    with pytest.raises(FileExistsError):
        recovery.seal(source,archive,keyring,'a'*40,gpg='not-called')
    assert archive.read_bytes()==b'keep this'
    (source/'config/travel.env').unlink()
    with pytest.raises(ValueError,match='mandatory rollback assets'):
        recovery.seal(source,tmp_path/'new.gpg',keyring,'a'*40,gpg='not-called')


def test_archive_parser_rejects_traversal():
    data = io.BytesIO()
    with tarfile.open(fileobj=data,mode='w') as archive:
        unsafe = tarfile.TarInfo('../untrusted')
        archive.addfile(unsafe)
    data.seek(0)
    with pytest.raises(ValueError,match='invalid member'):
        recovery.validate_plain_archive(data)


def test_inventory_completeness_checked_before_gpg(tmp_path):
    source = prepared(tmp_path)
    keyring = tmp_path / 'keyring'
    keyring.mkdir()
    output = tmp_path / 'approved.gpg'
    inventory_path = tmp_path / 'inventory.json'
    files = dict.fromkeys(recovery.REQUIRED | {'runtime/requirements.lock'})
    inventory_path.write_text(json.dumps({'format': 1, 'files': files}), encoding='utf-8')
    inventory = recovery.load_inventory(inventory_path)
    with pytest.raises(ValueError, match='file set differs'):
        recovery.seal(source, output, keyring, 'a'*40, gpg='not-called', inventory=inventory)
    unapproved = source / 'config/extra.env'
    unapproved.write_text('synthetic extra', encoding='utf-8')
    with pytest.raises(ValueError, match='file set differs'):
        recovery.seal(source, output, keyring, 'a'*40, gpg='not-called', inventory=dict.fromkeys(recovery.REQUIRED))
    assert not output.exists()


def test_collection_stop_check_requires_no_backend_listener(monkeypatch):
    unit = b'ActiveState=inactive\nMainPID=0\nNeedDaemonReload=no\n'
    results = iter((unit, b'', unit, b'LISTEN 0 128 0.0.0.0:8000'))
    monkeypatch.setattr(collector.subprocess, 'check_output', lambda *args, **kwargs: next(results))
    collector.require_stopped_travel()
    with pytest.raises(ValueError, match='no listener'):
        collector.require_stopped_travel()


@pytest.mark.skipif(os.name != 'nt', reason='Windows device path handling')
def test_bundle_extraction_rejects_reserved_device_paths(tmp_path):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w') as archive:
        archive.addfile(tarfile.TarInfo('config/NUL.txt'))
    data.seek(0)
    with pytest.raises(ValueError, match='Windows file path'):
        recovery.validate_plain_archive(data, restore_directory=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_recover_verified_sqlite_from_public_key_encrypted_archive(tmp_path):
    gpg = os.environ.get('TRAVEL_TEST_GPG') or shutil.which('gpg')
    if not gpg:
        pytest.skip('OpenPGP CLI required for the local encrypted roundtrip')
    prefix = (gpg,'--no-options','--homedir','.','--batch','--no-tty')
    agent = Path(gpg).with_name('gpgconf.exe' if Path(gpg).suffix=='.exe' else 'gpgconf')
    with tempfile.TemporaryDirectory(prefix='trg-key-') as keyring, tempfile.TemporaryDirectory(prefix='trg-pub-') as public:
        def run(folder, *args, data=None, check=True):
            result = subprocess.run((*prefix,*args),cwd=folder,input=data,capture_output=True,timeout=20)
            if check:
                assert result.returncode==0, result.stderr.decode('utf-8',errors='replace')
            return result

        try:
            run(keyring,'--pinentry-mode','loopback','--passphrase','','--quick-generate-key',
                'Travel Synthetic <recovery@example.invalid>','default','default','1d')
            result = run(keyring,'--with-colons','--list-keys')
            fingerprint = next(line.split(':')[9] for line in result.stdout.decode().splitlines() if line.startswith('fpr:'))
            public_key = run(keyring,'--export',fingerprint).stdout
            run(public,'--import',data=public_key)
            assert b'sec:' not in run(public,'--with-colons','--list-secret-keys',check=False).stdout

            source = prepared(tmp_path)
            db = sqlite3.connect(source / recovery.DATABASE)
            try:
                assert db.execute('PRAGMA journal_mode=WAL').fetchone() == ('wal',)
            finally:
                db.close()
            assert sorted(p.name for p in (source / 'data').iterdir()) == ['app.db']
            additional_assets = {
                'application/backend/app.py': b'# synthetic rollback code\n',
                'runtime/requirements.lock': b'synthetic==1.0\n',
                'systemd/travel.service.d/environment.conf': b'[Service]\n',
                'static/travel/assets/app.js': b'const fixture = true;\n',
                'data/photos/fixture.jpg': b'synthetic-attachment\x00\xff',
            }
            for name, content in additional_assets.items():
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            inventory = {name: hashlib.sha256(path.read_bytes()).hexdigest()
                         for name, path in recovery.archive_paths(source).items()}
            inventory[recovery.DATABASE] = inventory['config/travel.env'] = None
            inventory_path = tmp_path / 'inventory.json'
            inventory_path.write_text(json.dumps({'format': 1, 'files': inventory}), encoding='utf-8')
            inventory = recovery.load_inventory(inventory_path)
            changed_inventory = {**inventory, 'runtime/requirements.lock': 'a'*64}
            drifted = tmp_path / 'drifted.gpg'
            with pytest.raises(ValueError, match='file hash differs'):
                recovery.seal(source, drifted, public, fingerprint, gpg=gpg, inventory=changed_inventory)
            assert not drifted.exists()
            output = tmp_path/'travel-encrypted.gpg'
            info = recovery.seal(source, output, public, fingerprint, gpg=gpg, inventory=inventory)
            assert info['ciphertext_sha256']==hashlib.sha256(output.read_bytes()).hexdigest()
            copy = tmp_path/'separate-recovery'/'received.gpg'
            copy.parent.mkdir(mode=0o700)
            shutil.copy2(output,copy)
            verified = recovery.verify_encrypted(copy, keyring, info['ciphertext_sha256'], gpg=gpg)
            assert verified['verified'] and verified['members'] == len(recovery.REQUIRED | additional_assets.keys())
            with pytest.raises(ValueError, match='file hash differs'):
                recovery.verify_encrypted(copy, keyring, info['ciphertext_sha256'], gpg=gpg, inventory=changed_inventory)

            restored = copy.parent / 'restored.sqlite3'
            failed_database = tmp_path / 'failed-migration.db'
            # The sealed fixture has no sidecars and no open connections.
            shutil.copyfile(source / recovery.DATABASE, failed_database)
            failed = sqlite3.connect(failed_database)
            try:
                failed.execute('PRAGMA wal_autocheckpoint=0')
                failed.execute('ALTER TABLE example ADD COLUMN interrupted_change TEXT')
                failed.execute("UPDATE example SET interrupted_change = 'keep for diagnosis'")
                failed.commit()
                failed_paths = [failed_database, failed_database.with_name('failed-migration.db-wal')]
                observed = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in failed_paths}
                with pytest.raises(FileExistsError):
                    recovery.restore_database(copy, keyring, info['ciphertext_sha256'], failed_database,
                                              gpg=gpg, inventory=inventory)
                result = subprocess.run((sys.executable, recovery.__file__, 'restore-database',
                                         '--archive', str(copy), '--ciphertext-sha256', info['ciphertext_sha256'],
                                         '--gpg-home', keyring, '--gpg', gpg, '--output', str(restored),
                                         '--inventory', str(inventory_path)), capture_output=True, text=True, timeout=20)
                assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in failed_paths} == observed
                assert failed.execute('SELECT interrupted_change FROM example').fetchone() == ('keep for diagnosis',)
            finally:
                failed.close()
            assert result.returncode == 0, result.stderr
            assert 'restored_recovery_database' in result.stdout
            assert restored.read_bytes() == (source / recovery.DATABASE).read_bytes()
            assert {p.name for p in copy.parent.iterdir()} == {'received.gpg', 'restored.sqlite3'}
            with sqlite3.connect(restored) as db:
                assert [column[1] for column in db.execute('PRAGMA table_info(example)')] == ['msg', 'photo']
                assert db.execute('SELECT msg, photo FROM example').fetchall() == [
                    ('synthetic travel row', b'synthetic-photo')]
            with pytest.raises(FileExistsError):
                recovery.restore_database(copy, keyring, info['ciphertext_sha256'], restored, gpg=gpg)

            bundle = copy.parent / 'restored-bundle'
            result = subprocess.run((sys.executable, recovery.__file__, 'restore-bundle',
                                     '--archive', str(copy), '--ciphertext-sha256', info['ciphertext_sha256'],
                                     '--gpg-home', keyring, '--gpg', gpg, '--output', str(bundle),
                                     '--inventory', str(inventory_path)),
                                    capture_output=True, text=True, timeout=20)
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout)['scope'] == 'restored_recovery_bundle'
            expected = {name: path.read_bytes() for name, path in recovery.archive_paths(source).items()}
            actual = {path.relative_to(bundle).as_posix(): path.read_bytes() for path in bundle.rglob('*')
                      if path.is_file()}
            contents = json.loads(actual.pop(recovery.MANIFEST))
            assert expected == actual
            assert contents['files'] == {name: hashlib.sha256(content).hexdigest() for name, content in expected.items()}
            with pytest.raises(FileExistsError):
                recovery.restore_bundle(copy, keyring, info['ciphertext_sha256'], bundle, gpg=gpg)
            existing_empty = copy.parent / 'another-restore'
            existing_empty.mkdir()
            with pytest.raises(FileExistsError):
                recovery.restore_bundle(copy, keyring, info['ciphertext_sha256'], existing_empty, gpg=gpg)
            assert list(existing_empty.iterdir()) == []

            source_files = recovery.archive_paths(source)
            source_map = tmp_path / 'source-map.json'
            source_map.write_text(json.dumps({'format': 1, 'files': {n: str(p) for n, p in source_files.items()}}),
                                  encoding='utf-8')
            work = tmp_path / 'private-work'
            work.mkdir(mode=0o700)
            collected = copy.parent / 'collected.gpg'
            source_database = source_files[recovery.DATABASE]
            live = sqlite3.connect(source_database)
            try:
                live.execute('PRAGMA wal_autocheckpoint=0')
                live.execute('INSERT INTO example VALUES (?, ?)', ('wal commit', b'image-from-wal'))
                live.commit()
                live.execute('INSERT INTO example VALUES (?, ?)', ('uncommitted', b'do-not-restore'))
                sqlite_paths = [source_database, source_database.with_name('app.db-wal')]
                assert sqlite_paths[1].is_file()
                source_bytes = {path.name: path.read_bytes() for path in sqlite_paths}

                result = subprocess.run((sys.executable, collector.__file__, '--inventory', str(inventory_path),
                                         '--sources', str(source_map), '--work-parent', str(work),
                                         '--output', str(collected), '--gpg-home', public, '--gpg', gpg,
                                         '--recipient-fingerprint', fingerprint), capture_output=True, text=True, timeout=30)
                assert result.returncode == 0, result.stderr
                collected_report = json.loads(result.stdout)
                assert collected_report['sqlite_snapshot_verified'] and collected_report['source_metadata_stable']
                assert {path.name: path.read_bytes() for path in sqlite_paths} == source_bytes
                recovered = copy.parent / 'collected-db.sqlite3'
                recovery.restore_database(collected, keyring, collected_report['ciphertext_sha256'], recovered,
                                          gpg=gpg, inventory=inventory)
                with sqlite3.connect(recovered) as restored_db:
                    assert restored_db.execute('SELECT msg, photo FROM example ORDER BY rowid').fetchall() == [
                        ('synthetic travel row', b'synthetic-photo'), ('wal commit', b'image-from-wal')]

                guards = iter((True, False))

                def stopped():
                    if not next(guards):
                        raise ValueError('Synthetic writer restarted')

                refused = copy.parent / 'stopped-collection.gpg'
                with pytest.raises(ValueError, match='writer restarted'):
                    collector.collect(source_files, inventory, work, refused, public, fingerprint, gpg=gpg, guard=stopped)
                assert not refused.exists()
                assert list(work.iterdir()) == [] and list(copy.parent.glob('.travel-sealed-*')) == []
            finally:
                live.rollback()
                live.close()

            damage = bytearray(copy.read_bytes())
            damage[-1] ^= 1
            assert run(keyring,'--decrypt',data=damage,check=False).returncode!=0
            damaged = copy.with_name('damaged.gpg')
            damaged.write_bytes(damage)
            with pytest.raises(ValueError, match='differs from the recorded backup'):
                recovery.verify_encrypted(damaged, keyring, info['ciphertext_sha256'], gpg=gpg)
            damaged_digest = hashlib.sha256(damage).hexdigest()
            failed_restore = copy.parent / 'failed.sqlite3'
            with pytest.raises((ValueError, tarfile.TarError)):
                recovery.restore_database(damaged, keyring, damaged_digest, failed_restore, gpg=gpg)
            failed_bundle = copy.parent / 'failed-bundle'
            with pytest.raises((ValueError, tarfile.TarError)):
                recovery.restore_bundle(damaged, keyring, damaged_digest, failed_bundle, gpg=gpg)

            (source / recovery.DATABASE).write_bytes(b'valid archive member but invalid SQLite contents')
            invalid = tmp_path / 'invalid-database.gpg'
            invalid_info = recovery.seal(source, invalid, public, fingerprint, gpg=gpg)
            assert recovery.verify_encrypted(invalid, keyring, invalid_info['ciphertext_sha256'], gpg=gpg)['verified']
            with pytest.raises(sqlite3.DatabaseError):
                recovery.restore_database(invalid, keyring, invalid_info['ciphertext_sha256'], failed_restore, gpg=gpg)
            with pytest.raises(sqlite3.DatabaseError):
                recovery.restore_bundle(invalid, keyring, invalid_info['ciphertext_sha256'], failed_bundle, gpg=gpg)
            assert not failed_restore.exists()
            assert not failed_bundle.exists()
            assert list(copy.parent.glob('.travel-restore-*')) == []
        finally:
            if agent.is_file():
                for folder in (keyring,public):
                    subprocess.run((str(agent),'--homedir','.','--kill','gpg-agent'),
                                   cwd=folder,capture_output=True,timeout=8)
