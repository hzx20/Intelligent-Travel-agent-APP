"""Collect reviewed files and a SQLite snapshot directly into an encrypted backup."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import tempfile

if __package__:
    from .backup_db import backup_database
    from .seal_travel_backup import (DATABASE, MANIFEST, REQUIRED, DigestReader, check_inventory, checked_restore_output,
                                     load_inventory, private_recovery_directory, recovery_member_path, seal, valid_name)
else:
    from backup_db import backup_database
    from seal_travel_backup import (DATABASE, MANIFEST, REQUIRED, DigestReader, check_inventory, checked_restore_output,
                                    load_inventory, private_recovery_directory, recovery_member_path, seal, valid_name)


def read_sources(path):
    payload = json.loads(path.read_bytes())
    if not isinstance(payload, dict) or set(payload) != {'format', 'files'} or payload['format'] != 1:
        raise ValueError('Invalid backup source map')
    files = payload['files']
    if not isinstance(files, dict) or any(not isinstance(value, str) for value in files.values()):
        raise ValueError('Invalid backup source paths')
    return {name: Path(value) for name, value in files.items()}


def metadata(path):
    if not path.is_absolute():
        raise ValueError('Backup source must be an absolute path')
    info = path.lstat()
    if path.resolve(strict=True) != path or not stat.S_ISREG(info.st_mode):
        raise ValueError('Backup source must be a canonical regular file')
    return identity(info)


def identity(info):
    fields = (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns)
    # On Windows, path and handle stat can report different ctime meanings for the same file.
    return fields if os.name == 'nt' else (*fields, info.st_ctime_ns)


def copy_source(source, expected, output):
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    read_flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    fd = os.open(source, read_flags)
    with os.fdopen(fd, 'rb') as current:
        if identity(os.fstat(current.fileno())) != expected:
            raise ValueError('A backup source changed before collection')
        write_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0)
        target_fd = os.open(output, write_flags, 0o600)
        with os.fdopen(target_fd, 'wb') as target:
            reader = DigestReader(current)
            shutil.copyfileobj(reader, target, length=65536)
        if identity(os.fstat(current.fileno())) != expected or metadata(source) != expected:
            raise ValueError('A backup source changed during collection')
    return reader.digest.hexdigest()


def sqlite_sidecars(database):
    paths = (database.with_name(database.name + suffix) for suffix in ('-wal', '-journal'))
    return {path: metadata(path) for path in paths if os.path.lexists(path)}


def require_stopped_travel():
    unit = subprocess.check_output(['/usr/bin/systemctl', 'show', '--no-pager',
                                   '--property=ActiveState,MainPID,NeedDaemonReload', 'travel.service'], timeout=5)
    values = dict(line.split('=', 1) for line in unit.decode('utf-8').splitlines())
    listening = subprocess.check_output(['/usr/bin/ss', '-H', '-ltn', 'sport = :8000'], timeout=5)
    if (values != {'ActiveState': 'inactive', 'MainPID': '0', 'NeedDaemonReload': 'no'} or listening.strip()):
        raise ValueError('Expected stopped travel.service and no listener on TCP 8000')


def collect(sources, inventory, work_parent, output, gpg_home, recipient, *, gpg='gpg', guard=None):
    """Publish a verified ciphertext after a stable collection and a private SQLite copy."""
    check_inventory(sources, inventory)
    if not REQUIRED <= sources.keys() or any(not isinstance(name, str) or name == MANIFEST or not valid_name(name)
                                           for name in sources):
        raise ValueError('Invalid backup archive member name')
    output = checked_restore_output(output)
    work_parent = private_recovery_directory(work_parent)
    if guard is not None:
        guard()
    observations = {name: metadata(path) for name, path in sources.items()}
    database = sources[DATABASE]
    sidecars = sqlite_sidecars(database)

    with (tempfile.TemporaryDirectory(prefix='.travel-collect-', dir=work_parent) as scratch,
          tempfile.TemporaryDirectory(prefix='.travel-sealed-', dir=output.parent) as sealed):
        work = Path(scratch)
        prepared = work / 'prepared'
        prepared.mkdir(mode=0o700)
        for name, source in sorted(sources.items()):
            if name == DATABASE:
                continue
            checksum = copy_source(source, observations[name], recovery_member_path(prepared, name))
            if inventory[name] is not None and checksum != inventory[name]:
                raise ValueError('Collected file hash differs from the approved inventory')

        # Keep the real SQLite files untouched by opening only the private mirror with SQLite.
        mirror = work / 'sqlite-input' / database.name
        copy_source(database, observations[DATABASE], mirror)
        for path, expected in sidecars.items():
            copy_source(path, expected, mirror.parent / path.name)
        snapshot = work / 'sqlite-output' / 'app.db'
        snapshot.parent.mkdir(mode=0o700)
        backup_database(mirror, snapshot)
        copy_source(snapshot, metadata(snapshot), prepared / DATABASE)
        ciphertext = Path(sealed) / 'backup.gpg'
        report = seal(prepared, ciphertext, gpg_home, recipient, gpg=gpg, inventory=inventory)

        if observations != {name: metadata(path) for name, path in sources.items()} or sidecars != sqlite_sidecars(database):
            raise ValueError('Backup source set changed before ciphertext delivery')
        if guard is not None:
            guard()
        os.link(ciphertext, output)
    return {**report, 'scope': 'collected_encrypted_recovery_bundle',
            'sqlite_snapshot_verified': True, 'source_metadata_stable': True, 'write_stop_checked': guard is not None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('inventory', 'sources', 'work-parent', 'output', 'gpg-home'):
        parser.add_argument('--' + option, type=Path, required=True)
    parser.add_argument('--recipient-fingerprint', required=True)
    parser.add_argument('--gpg', default='gpg')
    parser.add_argument('--require-stopped-travel', action='store_true')
    args = parser.parse_args()
    try:
        sources = read_sources(args.sources)
        origin = Path('/opt/travel/app/backend/data/app.db')
        if os.name == 'posix' and sources.get(DATABASE) == origin and not args.require_stopped_travel:
            raise ValueError('Use the stopped-service check for the production database')
        report = collect(sources, load_inventory(args.inventory), args.work_parent, args.output,
                         args.gpg_home, args.recipient_fingerprint, gpg=args.gpg,
                         guard=require_stopped_travel if args.require_stopped_travel else None)
    except (OSError, ValueError, tarfile.TarError, sqlite3.Error, subprocess.SubprocessError) as error:
        parser.exit(1, f'Backup collection failed ({type(error).__name__})\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
