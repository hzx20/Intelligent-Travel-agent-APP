"""The application must use only a trusted proxy's client address for guest quotas."""
import asyncio

import httpx
from fastapi import FastAPI
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.routers import map as map_router


def test_guest_quota_with_trusted_and_untrusted_proxy(monkeypatch, tmp_path):
    from app.core.shared_limits import SharedPublicLimits, SharedQuota
    app = FastAPI()
    app.add_middleware(SharedPublicLimits, store=SharedQuota(tmp_path/'proxy-quotas.db'))
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts='192.0.2.10')
    app.include_router(map_router.router)
    monkeypatch.setattr(map_router.trip_routes, 'get_route', lambda *args: {'distance_m': 1})
    map_router._guest_route_peers.clear()
    body = {'origin': {'lng': 120, 'lat': 30}, 'destination': {'lng': 121, 'lat': 31}}

    async def exercise():
        trusted = httpx.ASGITransport(app=app, client=('192.0.2.10', 1234))
        untrusted = httpx.ASGITransport(app=app, client=('192.0.2.20', 1234))
        async with httpx.AsyncClient(transport=trusted, base_url='http://test') as proxy:
            a = {'X-Forwarded-For': '198.51.100.1'}
            b = {'X-Forwarded-For': '198.51.100.2'}
            for _ in range(30):
                assert (await proxy.post('/api/map/guest-route', headers=a, json=body)).status_code == 200
            assert (await proxy.post('/api/map/guest-route', headers=a, json=body)).status_code == 429
            assert (await proxy.post('/api/map/guest-route', headers=b, json=body)).status_code == 200
        async with httpx.AsyncClient(transport=untrusted, base_url='http://test') as direct:
            for i in range(30):
                spoof = {'X-Forwarded-For': f'203.0.113.{i + 1}'}
                assert (await direct.post('/api/map/guest-route', headers=spoof, json=body)).status_code == 200
            assert (await direct.post('/api/map/guest-route', headers={'X-Forwarded-For': '203.0.113.31'}, json=body)).status_code == 429

    try:
        asyncio.run(exercise())
    finally:
        map_router._guest_route_peers.clear()
