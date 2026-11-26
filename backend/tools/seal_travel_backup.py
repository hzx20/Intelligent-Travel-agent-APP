"""Seal a prepared recovery directory to an explicitly selected OpenPGP recipient."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import tempfile

if __package__:
    from .backup_db import verify_database
else:
    from backup_db import verify_database


MANIFEST = 'recovery-sha256.json'
DATABASE = 'data/app.db'
MANIFEST_LIMIT = 1024 * 1024
REQUIRED = {DATABASE, 'config/travel.env', 'systemd/travel.service',
            'nginx/nginx.conf', 'static/travel/index.html'}


def archive_paths(root):
    if root.resolve(strict=True) != root or not root.is_dir():
        raise ValueError('Expected a canonical recovery directory')
    files = {}
    for folder, directories, names in os.walk(root):
        parent = Path(folder)
        for name in directories:
            path = parent/name
            if path.resolve(strict=True) != path or not stat.S_ISDIR(path.lstat().st_mode):
                raise ValueError('Do not archive a linked directory')
        for name in names:
            path = parent/name
            info = path.lstat()
            if path.resolve(strict=True) != path or not stat.S_ISREG(info.st_mode):
                raise ValueError('Do not archive a linked or special file')
            relative = path.relative_to(root).as_posix()
            if not valid_name(relative) or relative == MANIFEST:
                raise ValueError('Unexpected archive path')
            files[relative] = path
    if not REQUIRED <= files.keys():
        raise ValueError('Prepared recovery directory is missing mandatory rollback assets')
    return files


def valid_name(name):
    path = PurePosixPath(name)
    return (bool(path.parts) and path.as_posix() == name and not path.is_absolute()
            and '\\' not in name and ':' not in name and all(part not in ('.','..') for part in path.parts))


def load_inventory(path):
    """Read the approved file set and hashes known before the snapshot."""
    payload = json.loads(path.read_bytes())
    if not isinstance(payload, dict) or set(payload) != {'format', 'files'} or payload['format'] != 1:
        raise ValueError('Invalid recovery inventory')
    files = payload['files']
    if (not isinstance(files, dict) or not REQUIRED <= files.keys() or MANIFEST in files
            or any(not valid_name(name) or (checksum is not None and
                   (not isinstance(checksum, str) or not re.fullmatch('[a-f0-9]{64}', checksum)))
                   for name, checksum in files.items())):
        raise ValueError('Invalid recovery inventory members')
    return files


def check_inventory(entries, inventory, *, compare_hashes=False):
    if inventory is None:
        return
    if entries.keys() != inventory.keys():
        raise ValueError('Recovery file set differs from the approved inventory')
    if compare_hashes and any(digest is not None and entries[name] != digest for name, digest in inventory.items()):
        raise ValueError('Recovery file hash differs from the approved inventory')


class DigestReader:
    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()

    def read(self, length):
        chunk = self.stream.read(length)
        self.digest.update(chunk)
        return chunk


def seal(root, output, gpg_home, recipient, *, gpg='gpg', inventory=None):
    root, output, gpg_home = (Path(path).absolute() for path in (root,output,gpg_home))
    if not re.fullmatch('[a-fA-F0-9]{40}|[a-fA-F0-9]{64}',recipient):
        raise ValueError('Pin the full recovery public-key fingerprint')
    if output.parent.resolve(strict=True) != output.parent or gpg_home.resolve(strict=True) != gpg_home:
        raise ValueError('Use canonical archive and keyring locations')
    if root in output.parents or root in gpg_home.parents or gpg_home in root.parents or root == gpg_home:
        raise ValueError('Keep output and the keyring outside the plaintext recovery directory')
    if os.path.lexists(output):
        raise FileExistsError('Recovery archive already exists')
    entries = archive_paths(root)
    check_inventory(entries, inventory)
    command = (gpg, '--no-options', '--homedir', '.', '--batch', '--no-tty', '--trust-model', 'always',
               '--recipient', recipient, '--output', '-', '--encrypt')
    tmp = tempfile.NamedTemporaryFile(dir=output.parent,prefix='.travel-recovery-',delete=False)
    temporary = Path(tmp.name)
    try:
        with tmp, subprocess.Popen(command,cwd=gpg_home,stdin=subprocess.PIPE,stdout=tmp,stderr=subprocess.DEVNULL) as encryption:
            digests = {}
            try:
                with encryption.stdin, tarfile.open(fileobj=encryption.stdin,mode='w|') as archive:
                    for name,path in sorted(entries.items()):
                        with path.open('rb') as stream:
                            length = os.fstat(stream.fileno()).st_size
                            member = tarfile.TarInfo(name)
                            member.size,member.mode = length,0o600
                            reader = DigestReader(stream)
                            archive.addfile(member,reader)
                            digests[name] = reader.digest.hexdigest()
                    check_inventory(digests, inventory, compare_hashes=True)
                    index = json.dumps({'format':1,'files':digests},sort_keys=True).encode('utf-8')
                    member = tarfile.TarInfo(MANIFEST)
                    member.size,member.mode = len(index),0o600
                    archive.addfile(member,io.BytesIO(index))
                if encryption.wait(timeout=120)!=0:
                    raise ValueError('OpenPGP encryption failed')
            except BaseException:
                encryption.kill()
                encryption.wait()
                raise
            tmp.flush()
            os.fsync(tmp.fileno())
        if temporary.stat().st_size<=0:
            raise ValueError('Encrypted archive is empty')
        with temporary.open('rb') as stream:
            digest = hashlib.file_digest(stream,'sha256').hexdigest()
        os.link(temporary,output)
        return {'scope':'encrypted_recovery_bundle','ciphertext_sha256':digest,
                'bytes':output.stat().st_size,'members':len(entries)}
    finally:
        temporary.unlink()


def recovery_member_path(directory, name):
    relative = PurePosixPath(name)
    if os.name == 'nt':
        is_reserved = getattr(os.path, 'isreserved', lambda part: PureWindowsPath(part).is_reserved())
        if any(is_reserved(part) or part.endswith(('.', ' ')) or
               any(ord(character) < 32 or character in '*?"<>|' for character in part) for part in relative.parts):
            raise ValueError('Recovery member is not a Windows file path')
    target = directory.joinpath(*relative.parts)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if target.parent.resolve().relative_to(directory.resolve()).as_posix() != relative.parent.as_posix():
        raise ValueError('Recovery directory name does not resolve exactly')
    return target


def restore_member(directory, name, content):
    target = recovery_member_path(directory, name)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
    with os.fdopen(fd, 'wb') as output:
        reader = DigestReader(content)
        shutil.copyfileobj(reader, output, length=65536)
        return reader.digest.hexdigest()


def validate_plain_archive(stream, *, database_copy=None, restore_directory=None, inventory=None):
    """Validate the manifest and optionally restore members in private staging."""
    entries = {}
    file_index = None
    seen = set()
    with tarfile.open(fileobj=stream,mode='r|*') as archive:
        for member in archive:
            if not member.isfile() or not valid_name(member.name) or member.name in seen:
                raise ValueError('Recovery archive has an invalid member')
            if inventory is not None and member.name != MANIFEST and member.name not in inventory:
                raise ValueError('Recovery file is outside the approved inventory')
            seen.add(member.name)
            with archive.extractfile(member) as content:
                if member.name == MANIFEST:
                    if member.size > MANIFEST_LIMIT:
                        raise ValueError('Recovery manifest exceeds the supported size')
                    index_bytes = content.read()
                    file_index = json.loads(index_bytes)
                    if restore_directory is not None:
                        restore_member(restore_directory, member.name, io.BytesIO(index_bytes))
                elif restore_directory is not None:
                    entries[member.name] = restore_member(restore_directory, member.name, content)
                elif member.name == DATABASE and database_copy is not None:
                    reader = DigestReader(content)
                    shutil.copyfileobj(reader, database_copy, length=65536)
                    entries[member.name] = reader.digest.hexdigest()
                else:
                    entries[member.name]=hashlib.file_digest(content,'sha256').hexdigest()
    if (not isinstance(file_index,dict) or file_index.get('format')!=1 or file_index.get('files')!=entries
            or not REQUIRED <= entries.keys()):
        raise ValueError('Recovery archive content fails the internal manifest')
    check_inventory(entries, inventory, compare_hashes=True)
    return len(entries)


def verify_encrypted(archive, gpg_home, expected_sha256, *, gpg='gpg', database_copy=None, restore_directory=None,
                     inventory=None):
    """Verify a retained ciphertext and its complete internal manifest."""
    if not re.fullmatch('[a-fA-F0-9]{64}', expected_sha256):
        raise ValueError('Expected a complete ciphertext SHA-256 digest')
    command = (gpg, '--no-options', '--homedir', '.', '--output', '-', '--decrypt')
    with archive.open('rb') as encrypted:
        digest = hashlib.file_digest(encrypted, 'sha256').hexdigest()
        if digest != expected_sha256.lower():
            raise ValueError('Ciphertext differs from the recorded backup')
        encrypted.seek(0)
        with subprocess.Popen(command, cwd=gpg_home, stdin=encrypted,
                              stdout=subprocess.PIPE) as decryption:
            try:
                members = validate_plain_archive(decryption.stdout, database_copy=database_copy,
                                                 restore_directory=restore_directory, inventory=inventory)
                # The tar reader stops at its end marker. Consume the final GPG output
                # and check its exit status before reporting authenticated plaintext.
                while decryption.stdout.read(65536):
                    pass
                if decryption.wait() != 0:
                    raise ValueError('OpenPGP decryption or integrity check failed')
            except BaseException:
                decryption.kill()
                raise
    return {'scope': 'encrypted_recovery_manifest', 'ciphertext_sha256': digest,
            'verified': True, 'members': members}


def private_recovery_directory(directory):
    directory = directory.absolute()
    if directory.resolve(strict=True) != directory or not directory.is_dir():
        raise ValueError('Use a canonical private restore directory')
    if os.name == 'posix':
        info = directory.stat()
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('Restore directory must be accessible only to the current user')
    return directory


def checked_restore_output(output):
    output = output.absolute()
    private_recovery_directory(output.parent)
    if os.path.lexists(output):
        raise FileExistsError('Restore output already exists')
    return output


def restore_database(archive, gpg_home, expected_sha256, output, *, gpg='gpg', inventory=None):
    """Restore SQLite to a new file inside an operator-selected private directory."""
    output = checked_restore_output(output)

    with tempfile.TemporaryDirectory(prefix='.travel-restore-', dir=output.parent) as work:
        staged = Path(work) / 'app.db'
        fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as database:
            report = verify_encrypted(archive, gpg_home, expected_sha256, gpg=gpg, database_copy=database,
                                      inventory=inventory)
            database.flush()
            os.fsync(database.fileno())
        verify_database(staged)
        size = staged.stat().st_size
        os.link(staged, output)
    return {**report, 'scope': 'restored_recovery_database', 'database_bytes': size,
            'sqlite_integrity_and_foreign_keys_verified': True}


def restore_bundle(archive, gpg_home, expected_sha256, output, *, gpg='gpg', inventory=None):
    """Restore the verified archive to a new directory under a private parent."""
    output = checked_restore_output(output)
    with tempfile.TemporaryDirectory(prefix='.travel-restore-', dir=output.parent) as work:
        staged = Path(work) / 'bundle'
        staged.mkdir(mode=0o700)
        report = verify_encrypted(archive, gpg_home, expected_sha256, gpg=gpg, restore_directory=staged,
                                  inventory=inventory)

        # SQLite may write WAL/SHM metadata during a read-only integrity check.
        validation = Path(work) / 'sqlite-check'
        validation.mkdir(mode=0o700)
        database = staged / DATABASE
        shutil.copyfile(database, validation / database.name)
        for suffix in ('-wal', '-shm', '-journal'):
            sidecar = database.with_name(database.name + suffix)
            if sidecar.is_file():
                shutil.copyfile(sidecar, validation / sidecar.name)
        verify_database(validation / database.name)

        # On POSIX, reserve the exact target first because rename permits replacing an empty directory.
        reserved = os.name == 'posix'
        if reserved:
            output.mkdir(mode=0o700)
        try:
            staged.rename(output)
        except BaseException:
            if reserved and staged.exists():
                output.rmdir()
            raise
    return {**report, 'scope': 'restored_recovery_bundle', 'sqlite_integrity_and_foreign_keys_verified': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command',required=True)
    encrypt = commands.add_parser('seal')
    encrypt.add_argument('--prepared',type=Path,required=True)
    encrypt.add_argument('--output',type=Path,required=True)
    encrypt.add_argument('--gpg-home',type=Path,required=True)
    encrypt.add_argument('--recipient-fingerprint',required=True)
    verify = commands.add_parser('verify')
    verify.add_argument('--decrypted',type=Path,required=True)
    verify_retained = commands.add_parser('verify-encrypted')
    restore = commands.add_parser('restore-database')
    restore.add_argument('--output', type=Path, required=True,
                         help='New database file in a private isolated directory')
    restore_all = commands.add_parser('restore-bundle')
    restore_all.add_argument('--output', type=Path, required=True,
                             help='New recovery directory under a private isolated parent')
    for command in (verify_retained, restore, restore_all):
        command.add_argument('--archive', type=Path, required=True)
        command.add_argument('--ciphertext-sha256', required=True)
        command.add_argument('--gpg-home', type=Path, required=True)
        command.add_argument('--gpg', default='gpg')
    for command in (encrypt, verify, verify_retained, restore, restore_all):
        command.add_argument('--inventory', type=Path, help='Approved exact file inventory with optional SHA-256 pins')
    args = parser.parse_args()
    try:
        inventory = load_inventory(args.inventory) if args.inventory is not None else None
        if args.command == 'seal':
            report = seal(args.prepared, args.output, args.gpg_home, args.recipient_fingerprint, inventory=inventory)
        elif args.command == 'verify-encrypted':
            report = verify_encrypted(args.archive, args.gpg_home, args.ciphertext_sha256, gpg=args.gpg,
                                      inventory=inventory)
        elif args.command == 'restore-database':
            report = restore_database(args.archive, args.gpg_home, args.ciphertext_sha256,
                                      args.output, gpg=args.gpg, inventory=inventory)
        elif args.command == 'restore-bundle':
            report = restore_bundle(args.archive, args.gpg_home, args.ciphertext_sha256, args.output,
                                    gpg=args.gpg, inventory=inventory)
        else:
            with args.decrypted.open('rb') as stream:
                report = {'scope': 'decrypted_recovery_manifest', 'verified': True,
                          'members': validate_plain_archive(stream, inventory=inventory)}
    except (OSError,ValueError,tarfile.TarError,subprocess.SubprocessError,sqlite3.Error) as error:
        parser.exit(1,f'Recovery bundle failed ({type(error).__name__})\n')
    print(json.dumps(report))


if __name__=='__main__':
    main()
