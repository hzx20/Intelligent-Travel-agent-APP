"""Bounded opt-in JSON cache with cross-process, fenced per-query leases."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time


_MISS = object()


class CacheUnavailable(Exception):
    pass


class SharedJSONCache:
    def __init__(self, path, *, clock=time.time, lease_seconds=20, wait_seconds=12,
                 max_entries=512, max_bytes=32*1024*1024, max_value=512*1024):
        self.path = Path(path)
        self.clock = clock
        self.lease_seconds = lease_seconds
        self.wait_seconds = wait_seconds
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.max_value = max_value

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=1.5, isolation_level=None)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS entries (key TEXT PRIMARY KEY, value TEXT NOT NULL, expires REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS leases (key TEXT PRIMARY KEY, token TEXT NOT NULL, expires REAL NOT NULL)')
            return db
        except BaseException:
            db.close()
            raise

    def _checked_value(self, row, validate):
        raw = row[0]
        if not isinstance(raw, str) or len(raw.encode()) > self.max_value:
            return _MISS
        try:
            value = json.loads(raw)
            if validate(value):
                return value
        except (ValueError, TypeError, KeyError):
            pass
        return _MISS

    def _read_live(self, db, digest, now, validate):
        row = db.execute('SELECT value FROM entries WHERE key=? AND expires>?', (digest, now)).fetchone()
        return _MISS if row is None else self._checked_value(row, validate)

    def _claim(self, db, digest, token, lease_seconds, validate):
        db.execute('BEGIN IMMEDIATE')
        now = self.clock()
        value = self._read_live(db, digest, now, validate)
        if value is not _MISS:
            db.commit()
            return value, False
        db.execute('DELETE FROM leases WHERE expires<=?', (now,))
        if db.execute('SELECT 1 FROM leases WHERE key=?', (digest,)).fetchone():
            db.commit()
            return _MISS, False
        # The requested entry is absent, expired or failed validation.
        db.execute('DELETE FROM entries WHERE expires<=? OR key=?', (now, digest))
        entries = db.execute('SELECT COUNT(*) FROM entries').fetchone()[0]
        leases = db.execute('SELECT COUNT(*) FROM leases').fetchone()[0]
        # Count pending loads so concurrent misses cannot all take the last slot.
        if entries + leases >= self.max_entries:
            raise CacheUnavailable('Cache capacity reached')
        if leases >= 128:
            raise CacheUnavailable('Cache is busy')
        db.execute('INSERT INTO leases VALUES(?,?,?)', (digest, token, now + lease_seconds))
        db.commit()
        return _MISS, True

    def _read_or_claim(self, digest, token, lease_seconds, validate):
        with closing(self.connect()) as db:
            now = self.clock()
            value = self._read_live(db, digest, now, validate)
            if value is not _MISS:
                return value, False
            # Waiters can observe an active load without contending with its publisher.
            if db.execute('SELECT 1 FROM leases WHERE key=? AND expires>?', (digest, now)).fetchone():
                return _MISS, False
            return self._claim(db, digest, token, lease_seconds, validate)

    @staticmethod
    def _retry_contention(operation, *args):
        for delay in (.05, .125):
            try:
                return operation(*args)
            except sqlite3.OperationalError as error:
                code = getattr(error, 'sqlite_errorcode', 0) & 0xff
                if code not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                    raise
                time.sleep(delay)
        return operation(*args)

    def _publish(self, digest, token, encoded, encoded_size, ttl, store_value):
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            now = self.clock()
            owned = db.execute('SELECT 1 FROM leases WHERE key=? AND token=? AND expires>?',
                               (digest, token, now)).fetchone()
            if not owned:
                raise CacheUnavailable('Query lease expired')
            if store_value:
                db.execute('DELETE FROM entries WHERE expires<=?', (now,))
                count, size = db.execute(
                    'SELECT COUNT(*),COALESCE(SUM(length(CAST(value AS BLOB))),0) FROM entries'
                ).fetchone()
                if count >= self.max_entries or size + encoded_size > self.max_bytes:
                    raise CacheUnavailable('Cache capacity reached')
                db.execute('INSERT OR REPLACE INTO entries VALUES(?,?,?)', (digest, encoded, now + ttl))
            db.execute('DELETE FROM leases WHERE key=? AND token=?', (digest, token))
            db.commit()

    def _release_failed_load(self, digest, token):
        try:
            with closing(self.connect()) as db:
                db.execute('DELETE FROM leases WHERE key=? AND token=?', (digest, token))
        except (OSError, sqlite3.Error):
            # The inactive token expires naturally if storage is still locked or unavailable.
            pass


    def get_or_load(self, key, loader, *, ttl, validate, cacheable=lambda value:True, lease_seconds=None):
        lease_seconds = self.lease_seconds if lease_seconds is None else lease_seconds
        digest = hashlib.sha256(json.dumps(key, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        token = secrets.token_hex(16)
        deadline = time.monotonic() + self.wait_seconds
        try:
            while True:
                value, claimed = self._retry_contention(self._read_or_claim, digest, token, lease_seconds, validate)
                if value is not _MISS:
                    return value, True
                if claimed:
                    break
                if time.monotonic() >= deadline:
                    raise CacheUnavailable('Query is still running')
                time.sleep(.05)
            try:
                value = loader()
                if not validate(value):
                    raise CacheUnavailable('Provider result is invalid')
                encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
                encoded_size = len(encoded.encode())
                if encoded_size > self.max_value:
                    raise CacheUnavailable('Provider result is too large')
                self._retry_contention(self._publish, digest, token, encoded, encoded_size, ttl, cacheable(value))
            except BaseException:
                self._release_failed_load(digest, token)
                raise
            return value, False
        except (OSError,sqlite3.Error,ValueError) as error:
            raise CacheUnavailable('Shared cache is unavailable') from error


def provider_cache():
    if os.environ.get('TRAVEL_SHARED_CACHE','').lower() not in ('1','true','yes'):return None
    from app.db.database import DATA_DIR
    return SharedJSONCache(DATA_DIR/'provider-cache.db')
