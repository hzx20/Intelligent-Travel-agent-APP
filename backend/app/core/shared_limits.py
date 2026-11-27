"""Opt-in shared sliding-window quotas for one SQLite-backed deployment."""
from contextlib import closing
from starlette.concurrency import run_in_threadpool
import hashlib
import math
from pathlib import Path
import sqlite3
import time

from starlette.responses import JSONResponse


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
# 规划类接口单独一档：一次规划 = 一次澄清 + 一次 embedding + 一次长文本生成
# （上游超时给到 240 秒），比地图查询贵两个数量级。不设限时游客可以无限提交，
# 上游配额和 2G 内存都会被拖垮。取消、轮询、读历史不在这里挡。
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
            try:
                retry = await run_in_threadpool(self.store.claim, scope['path'], peer, *rule)
            except LimitStoreUnavailable:
                response = JSONResponse({'detail':'请求额度暂时无法核对，请稍后重试。'}, status_code=503, headers={'Cache-Control':'no-store','Retry-After':'1'})
                return await response(scope, receive, send)
            if retry:
                response = JSONResponse({'detail':'请求较频繁，请稍后重试。'}, status_code=429, headers={'Cache-Control':'no-store','Retry-After':str(retry)})
                return await response(scope, receive, send)
        return await self.app(scope, receive, send)
