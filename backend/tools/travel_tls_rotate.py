"""Serialized certificate handoff. Callers supply a validated nginx and TLS probe."""
from contextlib import contextmanager
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import ssl
import stat
from subprocess import SubprocessError
import time


GENERATION = re.compile(r'[a-f0-9]{64}')
CERT = 'fullchain.pem'
KEY = 'privkey.pem'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def fingerprint(chain):
    require(isinstance(chain, bytes) and len(chain) <= 64*1024, 'Unexpected certificate chain length')
    m = re.match(rb'\s*(-----BEGIN CERTIFICATE-----[\s\S]+?-----END CERTIFICATE-----)', chain)
    require(m, 'Missing leaf certificate')
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(m[1].decode('ascii'))).hexdigest()


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY|os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def private_directory(path):
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0,
            'Certificate directory must be private, canonical and owned by operator')


class Store:
    def __init__(self, root):
        self.root = Path(root)
        require(self.root.is_absolute() and self.root.resolve(strict=True) == self.root, 'Use a canonical absolute store')
        private_directory(self.root)
        self.versions = self.root/'generations'
        self.versions.mkdir(mode=0o700, exist_ok=True)
        private_directory(self.versions)

    @contextmanager
    def locked(self):
        import fcntl
        lock = self.root/'.rotation.lock'
        fd = os.open(lock, os.O_NOFOLLOW | os.O_CREAT | os.O_RDWR, 0o600)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0,
                    'Invalid certificate lock file')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    def generation(self, name):
        require(isinstance(name,str) and GENERATION.fullmatch(name),'Invalid generation')
        path = self.versions/name
        private_directory(path)
        files = []
        for leaf, limit in ((CERT,64*1024),(KEY,16*1024)):
            p = path/leaf
            info = p.lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0
                    and 0 < info.st_size <= limit, 'Unexpected certificate file permissions, owner or size')
            files.append(p)
        require(fingerprint(files[0].read_bytes()) == name,'Generation does not identify its leaf')
        return files

    def current(self):
        target = os.readlink(self.root/'current')
        m = re.fullmatch('generations/([a-f0-9]{64})',target)
        require(m, 'Unexpected active generation pointer')
        self.generation(m[1])
        return m[1]

    def point(self, name):
        self.generation(name)
        active = self.root/'current'
        require(not os.path.lexists(active) or active.is_symlink(),'Refuse to replace a physical certificate directory')
        staged = self.root/('.pointer-'+secrets.token_hex(12))
        try:
            staged.symlink_to('generations/'+name)
            os.replace(staged,active)
            sync_dir(self.root)
        finally:
            staged.unlink(missing_ok=True)

    def stage(self, chain, key, validate_pair):
        name = fingerprint(chain)
        require(isinstance(key,bytes) and 0 < len(key) <= 16*1024, 'Unexpected private key length')
        dest = self.versions/name
        if os.path.lexists(dest):
            files = self.generation(name)
            require(files[0].read_bytes()==chain and hmac.compare_digest(files[1].read_bytes(),key),
                    'A previously used leaf cannot be replaced with different chain or key bytes')
            validate_pair(*files)
            return name
        temp = self.versions/('.new-'+secrets.token_hex(12))
        temp.mkdir(mode=0o700)
        try:
            for leaf,data in ((CERT,chain),(KEY,key)):
                fd = os.open(temp/leaf,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            validate_pair(temp/CERT,temp/KEY)
            sync_dir(temp)
            temp.rename(dest)
            sync_dir(self.versions)
        finally:
            if temp.exists():
                for leaf in (CERT,KEY):
                    (temp/leaf).unlink(missing_ok=True)
                temp.rmdir()
        return name


def wait_for_leaf(probe, expected, *, timeout=7):
    since, hits = time.monotonic(), 0
    while time.monotonic() - since < timeout:
        try:
            hits = hits+1 if probe()==expected else 0
        except (OSError,RuntimeError,ValueError,SubprocessError):
            hits = 0
        if hits>=2:
            return True
        time.sleep(.12)
    return False


def switch(store, chain, key, *, validate_pair, check_nginx, reload_nginx, peer, wait=wait_for_leaf):
    """Rotate only a known-good running edge. Initial deployment is a distinct operation."""
    with store.locked():
        old = store.current()
        if not wait(peer,old):
            return {'state':'old_leaf_unconfirmed','reload_sent':False,'rollback_verified':False}
        new = store.stage(chain,key,validate_pair)
        if old == new:
            return {'state':'unchanged','reload_sent':False,'rollback_verified':False}
        attempted_reload = False
        try:
            store.point(new)
            check_nginx()
            attempted_reload = True
            reload_nginx()
            if wait(peer,new):
                return {'state':'switched','reload_sent':True,'rollback_verified':False}
        except (OSError,RuntimeError,ValueError,SubprocessError):
            pass
        try:
            store.point(old)
            # A reload may have taken effect even if its command or observation failed.
            if attempted_reload:
                check_nginx()
                reload_nginx()
            restored = wait(peer,old)
        except (OSError,RuntimeError,ValueError,SubprocessError):
            restored = False
        return {'state':'restored_previous' if restored else 'rollback_unconfirmed',
                'reload_sent':attempted_reload,'rollback_verified':restored}
