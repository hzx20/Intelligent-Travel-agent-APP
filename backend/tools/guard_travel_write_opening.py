"""Persist the point at which a release can begin accepting business writes."""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess


def check_directory(path):
    directory = path.parent
    if directory.resolve(strict=True) != directory or not directory.is_dir():
        raise ValueError('Use a canonical directory for the cutover record')
    if os.name == 'posix':
        info = directory.stat()
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('The cutover record must be accessible only to its owner')


def mark_opening(path, application_id, backup_sha256):
    """A failed or interrupted write must still leave the conservative marker."""
    if any(not re.fullmatch('[a-f0-9]{64}', value) for value in (application_id, backup_sha256)):
        raise ValueError('Use full reviewed application and backup identifiers')
    check_directory(path)
    record = {'format': 1, 'application_id': application_id, 'rollback_backup_sha256': backup_sha256,
              'state': 'new_writes_may_have_been_accepted'}
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(path, flags, 0o600), 'w', encoding='utf-8') as stream:
        json.dump(record, stream, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    if os.name == 'posix':
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return record


def recovery_policy(path):
    """Only positive proof of the write gate can justify restoring an old backup."""
    check_directory(path)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return {'state': 'opening_record_absent', 'preserve_current_database': True,
                'need_independent_proof_no_new_writes': True, 'old_snapshot_overwrite_allowed': False}
    if stat.S_ISREG(mode) and not path.is_symlink():
        try:
            with path.open('rb') as stream:
                raw = stream.read(1025)
            record = json.loads(raw) if len(raw) <= 1024 else None
        except (OSError, ValueError):
            record = None
        if isinstance(record, dict) and record.get('state') == 'new_writes_may_have_been_accepted':
            return {'state': record['state'], 'preserve_current_database': True, 'old_snapshot_overwrite_allowed': False}
    return {'state': 'opening_record_unconfirmed', 'preserve_current_database': True,
            'old_snapshot_overwrite_allowed': False}


def run_opening(path, application_id, backup_sha256, command):
    """Run an already reviewed command only after recording possible exposure."""
    if not command or not os.path.isabs(command[0]):
        raise ValueError('Supply the exact opening command with an absolute executable path')
    mark_opening(path, application_id, backup_sha256)
    return subprocess.run(command, check=False).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', type=Path, required=True)
    actions = parser.add_subparsers(dest='action', required=True)
    actions.add_parser('recovery-policy')
    opening = actions.add_parser('open-reviewed-entry')
    opening.add_argument('--application-id', required=True)
    opening.add_argument('--backup-sha256', required=True)
    opening.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    record = args.record.absolute()
    if args.action == 'recovery-policy':
        print(json.dumps(recovery_policy(record)))
        return 0
    command = args.command[1:] if args.command and args.command[0] == '--' else args.command
    try:
        return run_opening(record, args.application_id, args.backup_sha256, command)
    except (OSError, ValueError) as error:
        parser.exit(1, f'Opening did not complete ({type(error).__name__}). Inspect the cutover record before recovery.\n')


if __name__ == '__main__':
    raise SystemExit(main())
