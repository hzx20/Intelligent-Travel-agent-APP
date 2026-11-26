"""Create and verify a SQLite snapshot without overwriting any existing file.

Restore rehearsal uses the same operation: source=backup, output=a NEW database.
This module deliberately does not import the application or run schema migrations.
"""
import argparse
from contextlib import closing
import math
import os
from pathlib import Path
import sqlite3
import time


def verify_database(path, *, deadline=None):
    def expired():
        return deadline is not None and time.monotonic() >= deadline
    if expired():
        raise TimeoutError('Database verification timed out')
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
        db.set_progress_handler(lambda: int(expired()), 1000)
        try:
            result = db.execute('PRAGMA integrity_check').fetchall()
            if result != [('ok',)]:
                raise ValueError('SQLite integrity check failed')
            if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise ValueError('SQLite foreign key check failed')
            if expired():
                raise TimeoutError('Database verification timed out')
        except sqlite3.OperationalError as error:
            if expired():
                raise TimeoutError('Database verification timed out') from error
            raise
        finally:
            db.set_progress_handler(None, 0)


def backup_database(source, output, timeout_seconds=60):
    source, output = Path(source).resolve(), Path(output).absolute()
    if source == output.resolve():
        raise ValueError('Source and output must be different files')
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError('Timeout must be a positive finite number')
    deadline = time.monotonic() + timeout_seconds
    def progress(status, remaining, total):
        if time.monotonic() >= deadline:
            raise TimeoutError('Backup timed out; no completed snapshot was saved')

    # mode=ro fails for a missing source instead of silently creating an empty DB.
    source_db = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=1)
    created = False
    try:
        # O_EXCL prevents replacing an older backup, a restore target or a symlink.
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        created = True
        destination_db = sqlite3.connect(output)
        try:
            source_db.backup(destination_db, pages=128, progress=progress, sleep=0.1)
        finally:
            destination_db.close()
        verify_database(output, deadline=deadline)
    except BaseException:
        if created:
            output.unlink(missing_ok=True)
        raise
    finally:
        source_db.close()
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--output', type=Path, help='Must not already exist')
    action.add_argument('--verify-only', action='store_true', help='Check an existing database without copying or migrating it')
    parser.add_argument('--timeout', type=float, default=60, help='Backup deadline in seconds')
    args = parser.parse_args()
    try:
        if args.verify_only:
            if not math.isfinite(args.timeout) or args.timeout <= 0:
                raise ValueError('Timeout must be a positive finite number')
            verify_database(args.source, deadline=time.monotonic() + args.timeout)
            print('Verified SQLite integrity and foreign keys (read-only)')
            return
        output = backup_database(args.source, args.output, args.timeout)
    except (OSError, sqlite3.Error, ValueError) as error:
        parser.exit(1, f'Backup failed: {error}\n')
    print(f'Verified SQLite snapshot: {output} ({output.stat().st_size} bytes)')


if __name__ == '__main__':
    main()
