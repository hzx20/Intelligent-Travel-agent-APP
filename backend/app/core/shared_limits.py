"""Opt-in shared sliding-window quotas for one SQLite-backed deployment."""
from contextlib import closing
from starlette.concurrency import run_in_threadpool
import hashlib
import ipaddress
import math
from pathlib import Path
import sqlite3
import time

from starlette.responses import JSONResponse


def _shared_peer(peer: str) -> bool:
    """这个 peer 是否代表"一群人"而不是一个用户。

    反向代理没把真实 IP 传下来时，scope.client 会是 127.0.0.1 或 172.16/10.x
    这类地址：所有用户共用一个 key。按它限流等于全站共享同一个额度——
    5 次/分钟会把整个站点挡死。这种地址宁可不限，也不误伤。
    """
    try:
        addr = ipaddress.ip_address(str(peer))
    except ValueError:
        # 解析不出地址（空值、"unknown"）就没法区分用户，一律当作共享
        return True
    return addr.is_loopback or addr.is_private or addr.is_link_local


class LimitStoreUnavailable(Exception):
    pass


class SharedQuota:
    def __init__(self, path, *, max_events=100000, clock=time.time):
        self.path = Path(path)
        self.max_events = max_events
        self.clock = clock

    def claim(self, namespace, peer, limit, window):
        if limit < 1 or window <= 0:
            raise ValueError('Invalid quota')
        key = hashlib.sha256((namespace+'\0'+peer).encode()).hexdigest()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=0.5, isolation_level=None)) as db:
                db.execute('CREATE TABLE IF NOT EXISTS quota_events (id INTEGER PRIMARY KEY, scope TEXT NOT NULL, expires REAL NOT NULL)')
                db.execute('CREATE INDEX IF NOT EXISTS ix_quota_expiry ON quota_events(expires)')
                db.execute('CREATE INDEX IF NOT EXISTS ix_quota_scope ON quota_events(scope, expires)')
                db.execute('BEGIN IMMEDIATE')
                now = self.clock()
                db.execute('DELETE FROM quota_events WHERE expires <= ?', (now,))
                count, first = db.execute('SELECT COUNT(*), MIN(expires) FROM quota_events WHERE scope=?', (key,)).fetchone()
                if count >= limit:
                    db.rollback()
                    return max(1, math.ceil(first-now))
                if db.execute('SELECT COUNT(*) FROM quota_events').fetchone()[0] >= self.max_events:
                    db.rollback()
                    raise LimitStoreUnavailable('Quota storage is full')
                db.execute('INSERT INTO quota_events(scope, expires) VALUES (?, ?)', (key, now+window))
                db.commit()
                return 0
        except (OSError, sqlite3.Error) as error:
            raise LimitStoreUnavailable('Quota storage is unavailable') from error


# Apply before request-body reads. Existing per-process capacity/limits remain.
# Polling, cancellation, account/session checks and reads are not covered here.
#
# 注意：线上生产环境（travel-https-edge）的 nginx 已经按真实公网 IP 对
# /travel/api/plan/stream、/plan/jobs、/plan/jobs/create 做了 6r/m + burst 2 的限流，
# 这里再列一遍只是给"没有反向代理、直接跑 uvicorn"的场景兜底。默认不启用
# （TRAVEL_SHARED_LIMITS=0），两道叠加会把实际额度压到更严的那一道。
PUBLIC_QUOTAS = {
    '/api/map/guest-route': (30, 60),
    '/api/map/place-search': (30, 60),
    '/api/trips/guest-guide-image-text': (2, 60),
    '/api/trips/guest-ai-preview': (2, 60),
    '/api/trips/guest-guide-preview': (4, 60),
    '/api/plan/stream': (5, 60),
    '/api/plan/jobs': (5, 60),
    '/api/plan/jobs/create': (5, 60),
    '/api/clarify': (20, 60),
    '/api/itinerary': (6, 60),
}


class SharedPublicLimits:
    def __init__(self, app, store):
        self.app, self.store = app, store

    async def __call__(self, scope, receive, send):
        rule = PUBLIC_QUOTAS.get(scope.get('path')) if scope.get('type') == 'http' and scope.get('method') == 'POST' else None
        if rule:
            # Uvicorn's configured trusted-proxy handling has already resolved
            # scope.client. Never trust forwarded headers directly here.
            peer = (scope.get('client') or ('unknown', 0))[0]
            if _shared_peer(peer):
                # 无法区分到具体用户，限流只会挡住所有人
                return await self.app(scope, receive, send)
            try:
                retry = await run_in_threadpool(self.store.claim, scope['path'], peer, *rule)
            except LimitStoreUnavailable:
                response = JSONResponse({'detail':'请求额度暂时无法核对，请稍后重试。'}, status_code=503, headers={'Cache-Control':'no-store','Retry-After':'1'})
                return await response(scope, receive, send)
            if retry:
                response = JSONResponse({'detail':'请求较频繁，请稍后重试。'}, status_code=429, headers={'Cache-Control':'no-store','Retry-After':str(retry)})
                return await response(scope, receive, send)
        return await self.app(scope, receive, send)
