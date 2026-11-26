import json
from datetime import date

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.core.shared_cache import SharedJSONCache
from app.services import qweather, variflight, weather
from app.services.supplier_http import SupplierError, request_json
from app.routers import travel_data


@pytest.fixture(autouse=True)
def supplier_settings(monkeypatch):
    monkeypatch.setattr(settings, "qweather_api_host", "test.re.qweatherapi.com")
    monkeypatch.setattr(settings, "qweather_api_key", "synthetic-weather-key")
    monkeypatch.setattr(settings, "variflight_api_key", "synthetic-flight-key")
    monkeypatch.delenv("TRAVEL_PROVIDER_BUDGET", raising=False)
    monkeypatch.delenv("TRAVEL_SHARED_CACHE", raising=False)
    qweather._cache.clear()
    travel_data._counts.clear()


def forecast():
    return {"metadata": {"attributions": ["https://developer.qweather.com/attribution.html"]},
            "days": [{"forecastStartTime": "2026-11-16T16:00:00Z",
                      "temperatureMin": {"value": 15, "unit": "°C"},
                      "temperatureMax": {"value": 24.5, "unit": "°C"},
                      "daytime": {"forecastStartTime": "2026-11-17T07:00:00+08:00", "condition": {"text": "晴"}},
                      "nighttime": {"condition": {"text": "多云"}}}]}


def weather_transport(calls, daily=None, places=None):
    def respond(request):
        calls.append(request)
        assert request.headers["X-QW-Api-Key"] == "synthetic-weather-key"
        assert "key" not in request.url.params
        if request.url.path == "/geo/v2/city/lookup":
            return httpx.Response(200, json={"code": "200", "location": places if places is not None else [
                {"name": "杭州", "lat": "30.27415", "lon": "120.15515"}]})
        assert request.url.path == "/weather/v1/daily/30.27/120.16"
        assert request.url.params["localTime"] == "true"
        return httpx.Response(200, json=forecast() if daily is None else daily)
    return httpx.MockTransport(respond)


def test_weather_coordinates_local_date_attribution_and_cache():
    calls = []
    with httpx.Client(transport=weather_transport(calls)) as client:
        result = qweather.fetch_weather("杭州", client=client)
        assert result["casts"][0] == {"date": "2026-11-17", "day": "晴", "night": "多云", "temp_low": "15", "temp_high": "24.5"}
        assert result["provider"] == "qweather" and result["attributions"]
        result["casts"].clear()
        assert qweather.fetch_weather("杭州", client=client)["casts"]
    assert len(calls) == 2


def test_weather_shared_cache_preserves_expiry_and_separates_credentials(tmp_path, monkeypatch):
    from contextlib import closing

    now = [100]
    cache = SharedJSONCache(tmp_path / 'weather.db', clock=lambda: now[0])
    monkeypatch.setattr(qweather, 'provider_cache', lambda: cache)
    calls = []

    def fetch(city, client, host, key):
        calls.append((city, host, key))
        now[0] += 25  # Combined GeoAPI and forecast latency exceeds the usual lease.
        return {'ok': True, 'city': city, 'provider': 'qweather', 'source': '和风天气',
                'attributions': ['weather fixture'],
                'casts': [{'date': '2026-11-17', 'day': '晴', 'night': '多云', 'temp_low': '15', 'temp_high': '24.5'}]}

    monkeypatch.setattr(qweather, '_fetch', fetch)
    first = qweather.fetch_weather('杭州')
    first['casts'].clear()
    now[0] = 1024
    assert qweather.fetch_weather('杭州')['casts']
    assert len(calls) == 1
    now[0] = 1025
    assert qweather.fetch_weather('杭州')['casts']
    assert len(calls) == 2
    monkeypatch.setattr(settings, 'qweather_api_key', 'rotated-synthetic-key')
    assert qweather.fetch_weather('杭州')['casts']
    monkeypatch.setattr(settings, 'qweather_api_host', 'another.qweatherapi.com')
    assert qweather.fetch_weather('杭州')['casts']
    assert len(calls) == 4
    with closing(cache.connect()) as db:
        entries = db.execute('SELECT key, value FROM entries').fetchall()
    assert len(entries) == 3
    assert all(len(key) == 64 for key, _ in entries)
    assert not any(secret in f'{key}{value}' for key, value in entries
                   for secret in ('synthetic-weather-key', 'rotated-synthetic-key'))


def test_weather_shared_cache_upstream_failure_recovers_and_broken_store_stops_requests(tmp_path, monkeypatch):
    cache = SharedJSONCache(tmp_path / 'weather.db')
    monkeypatch.setattr(qweather, 'provider_cache', lambda: cache)
    calls = []
    with httpx.Client(transport=weather_transport(calls, daily={'code': '403'})) as client:
        with pytest.raises(SupplierError, match='预报格式异常'):
            qweather.fetch_weather('杭州', client=client)
    with httpx.Client(transport=weather_transport(calls)) as client:
        assert qweather.fetch_weather('杭州', client=client)['casts']
        assert len(calls) == 4
        cache.path.write_bytes(b'broken sqlite cache')
        with pytest.raises(SupplierError, match='天气缓存暂不可用') as error:
            qweather.fetch_weather('杭州', client=client)
        assert error.value.status_code == 503
    assert len(calls) == 4


@pytest.mark.parametrize("host", ["localhost", "qweatherapi.com", "evil.test/qweatherapi.com",
    "https://test.qweatherapi.com", "test.qweatherapi.com:443", "test.qweatherapi.com.evil.test", "a@b.qweatherapi.com"])
def test_invalid_host_rejected_before_network(host, monkeypatch):
    monkeypatch.setattr(settings, "qweather_api_host", host)
    with pytest.raises(SupplierError, match="API Host"):
        qweather.fetch_weather("杭州")


def test_ambiguous_city_does_not_guess():
    calls = []
    places = [{"name": "鼓楼", "lat": "1", "lon": "1"}, {"name": "鼓楼", "lat": "2", "lon": "2"}]
    with httpx.Client(transport=weather_transport(calls, places=places)) as client:
        with pytest.raises(SupplierError, match="同名"):
            qweather.fetch_weather("鼓楼", client=client)
    assert len(calls) == 1


@pytest.mark.parametrize("daily", [{"days": []}, {"days": "wrong"}, {"code": "403"}])
def test_bad_weather_never_becomes_success(daily):
    with httpx.Client(transport=weather_transport([], daily=daily)) as client:
        with pytest.raises(SupplierError):
            qweather.fetch_weather("杭州", client=client)


def test_configured_weather_failure_does_not_silently_use_amap(monkeypatch):
    def fail(city):
        raise SupplierError("测试故障")
    monkeypatch.setattr(qweather, "fetch_weather", fail)
    monkeypatch.setattr(weather, "amap_get", lambda *a, **k: pytest.fail("unexpected fallback"))
    assert weather.fetch_weather(city_name="杭州") == {"ok": False, "city": "杭州", "provider": "qweather", "error": "测试故障"}


def test_missing_weather_key_preserves_existing_amap_path(monkeypatch):
    monkeypatch.setattr(settings, "qweather_api_key", "")
    monkeypatch.setattr(weather, "amap_get", lambda *a, **k: httpx.Response(200, json={"status": "1", "forecasts": [{"city": "杭州", "casts": []}]}))
    assert weather.fetch_weather(city_name="杭州")["ok"] is True


@pytest.mark.parametrize("kind, fields, tool, args", [
    ("number", {"flight_number": "MU2157"}, "searchFlightsByNumber", {"date": "2026-11-17", "fnum": "MU2157"}),
    ("route", {"origin": "SHA", "destination": "BJS"}, "searchFlightsByDepArr", {"date": "2026-11-17", "depcity": "SHA", "arrcity": "BJS", "limit": 20, "offset": 0, "detail": "summary"}),
    ("prices", {"origin": "SHA", "destination": "BJS"}, "getFlightPriceByCities", {"dep_date": "2026-11-17", "dep_city": "SHA", "arr_city": "BJS"}),
])
def test_flight_tools_auth_arguments_no_retry(kind, fields, tool, args):
    calls = []
    def respond(request):
        calls.append(request)
        assert str(request.url) == variflight.ENDPOINT
        assert request.headers["X-API-Key"] == "synthetic-flight-key"
        payload = json.loads(request.content)
        assert payload == {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "synthetic result"}]}})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = variflight.query_flights(variflight.FlightQuery(kind=kind, date=date(2026, 11, 17), **fields), client=client)
    assert result["texts"] == ["synthetic result"] and result["source"] == "飞常准"
    assert len(calls) == 1


@pytest.mark.parametrize("patch", [
    {"kind": "unknown"}, {"flight_number": ""}, {"flight_number": "../secret"}, {"tool": "bookFlight"},
    {"date": "bad"}, {"kind": "route", "origin": "SHA", "destination": "SHA"},
    {"origin": "PVG"},
])
def test_flight_invalid_input_cannot_call_arbitrary_tools(patch):
    with pytest.raises(ValidationError):
        variflight.FlightQuery(**{"date": "2026-11-17", "flight_number": "MU2157", **patch})


@pytest.mark.parametrize("body", [
    {"jsonrpc": "2.0", "id": 1, "error": {"message": "secret error"}},
    {"jsonrpc": "2.0", "id": 1, "result": {"isError": True}},
    {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "Error executing tool secret"}]}},
    {"jsonrpc": "2.0", "id": 2, "result": {"content": []}},
])
def test_mcp_errors_are_sanitized(body):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))) as client:
        with pytest.raises(SupplierError) as caught:
            variflight.query_flights(variflight.FlightQuery(date="2026-11-17", flight_number="MU2157"), client=client)
        assert "secret" not in str(caught.value)


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
def test_transport_failure_is_bounded_and_sanitized(status):
    calls = []
    def respond(request):
        calls.append(1)
        return httpx.Response(status, text="synthetic secret", headers={"location": "https://evil.invalid"})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(SupplierError) as caught:
            request_json(client, "GET", "https://test.invalid")
    assert len(calls) == 1 and "secret" not in str(caught.value)


def test_transport_response_size_limit():
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 50))) as client:
        with pytest.raises(SupplierError, match="过大"):
            request_json(client, "GET", "https://test.invalid", max_bytes=20)


def test_query_rate_limit_and_user_isolation():
    from fastapi import HTTPException
    for _ in range(6):
        travel_data._limit(1, "flights")
    with pytest.raises(HTTPException) as caught:
        travel_data._limit(1, "flights")
    assert caught.value.status_code == 429
    travel_data._limit(2, "flights")
    travel_data._limit(1, "weather")


def test_authenticated_routes_and_no_supplier_calls_for_status(monkeypatch):
    from fastapi import Response, HTTPException
    from types import SimpleNamespace
    from app.main import app
    from app.routers.auth import get_current_user
    assert {"/api/travel-data/status", "/api/travel-data/weather", "/api/travel-data/flights/query"} <= set(app.openapi()["paths"])
    routes = travel_data.router.routes
    assert len(routes) == 3
    assert all(any(d.call is get_current_user for d in r.dependant.dependencies) for r in routes)
    with pytest.raises(HTTPException) as denied:
        get_current_user(cred=None, db=None)
    assert denied.value.status_code == 401
    monkeypatch.setattr(qweather, "fetch_weather", lambda city: {"ok": True, "city": city})
    monkeypatch.setattr(variflight, "query_flights", lambda query: {"source": "飞常准"})
    response = Response()
    result = travel_data.supplier_status(response)
    assert result == {"qweather": {"configured": True}, "variflight": {"configured": True}}
    assert "synthetic" not in str(result) and response.headers["cache-control"] == "no-store"
    user = SimpleNamespace(id=1)
    assert travel_data.weather_query(Response(), "杭州", user)["city"] == "杭州"
    response = Response()
    result = travel_data.flight_query(variflight.FlightQuery(date="2026-11-17", flight_number="MU2157"), response, user)
    assert result["source"] == "飞常准" and response.headers["cache-control"] == "no-store"


def test_missing_credentials_cannot_make_network_calls(monkeypatch):
    monkeypatch.setattr(settings, "qweather_api_key", "")
    monkeypatch.setattr(settings, "variflight_api_key", "")
    with pytest.raises(SupplierError) as error:
        qweather.fetch_weather("杭州")
    assert error.value.status_code == 503
    with pytest.raises(SupplierError) as error:
        variflight.query_flights(variflight.FlightQuery(date="2026-11-17", flight_number="MU2157"))
    assert error.value.status_code == 503


def test_http_authorization_validation_and_query_result(client, monkeypatch):
    from app.db.models import User
    from app.security import create_token
    c, factory = client
    for method, path, body in [
        ("GET", "/api/travel-data/status", None),
        ("GET", "/api/travel-data/weather?city=杭州", None),
        ("POST", "/api/travel-data/flights/query", {"date": "2026-11-17", "flight_number": "MU2157"}),
    ]:
        assert c.request(method, path, json=body).status_code == 401
    with factory() as db:
        user = User(username="supplier_http", password_hash="synthetic-unused")
        db.add(user); db.commit(); uid = user.id
    headers = {"Authorization": "Bearer " + create_token(uid)}
    calls = []
    def query(body):
        calls.append(body.kind)
        return {"source": "飞常准", "texts": ["synthetic"]}
    monkeypatch.setattr(variflight, "query_flights", query)
    assert c.get("/api/travel-data/status", headers=headers).status_code == 200
    response = c.post("/api/travel-data/flights/query", headers=headers,
                      json={"date": "2026-11-17", "flight_number": "MU2157", "tool": "book"})
    assert response.status_code == 422 and not calls
    response = c.post("/api/travel-data/flights/query", headers=headers,
                      json={"date": "2026-11-17", "flight_number": "MU2157"})
    assert response.status_code == 200 and response.json()["source"] == "飞常准"
    assert response.headers["cache-control"] == "no-store" and calls == ["number"]
