"""Exercise strict model replies through authenticated HTTP and durable jobs."""
import json
import time

import pytest

from app.db.trip_models import TripAiJob, TripProposal
from app.services import trip_ai
from test_trip_ai import setup_trip
from test_trips import account


@pytest.fixture(autouse=True)
def no_real_model(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Real model access is forbidden in schema HTTP tests")

    monkeypatch.setattr(trip_ai, "chat", forbidden)


def wait_for_job(c, path, owner, status):
    deadline = time.monotonic() + 8
    while True:
        response = c.get(path + "/latest", headers=owner)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        job = response.json()["job"]
        if job and job["status"] == status:
            return job
        assert time.monotonic() < deadline, job
        time.sleep(.01)


def test_http_rejects_boolean_target_without_saving_then_allows_valid_preview(client, monkeypatch):
    c, sessions = client
    owner = account(c, "schema_http_owner")
    stranger = account(c, "schema_http_other")
    trip = setup_trip(c, owner)
    path = f"/api/trips/{trip['id']}/ai-proposals"
    payload = {"version": trip["version"], "request": "调整第一项备注"}
    calls = []

    async def invalid_chat(*args, **kwargs):
        calls.append("invalid")
        return '{"operations":[{"action":"remove","item_id":true}]}'

    monkeypatch.setattr(trip_ai, "chat", invalid_chat)
    assert c.post(path, json=payload).status_code == 401
    assert c.post(path, headers=stranger, json=payload).status_code == 404
    assert calls == []
    assert c.post(path, headers=owner, json=payload).status_code == 502
    assert calls == ["invalid", "invalid"]
    assert c.get(f"/api/trips/{trip['id']}", headers=owner).json() == trip
    assert c.get(path + "/latest", headers=owner).json() == {"proposal": None}
    with sessions() as db:
        assert db.query(TripProposal).count() == 0

    target = trip["days"][0]["items"][0]

    async def valid_chat(*args, **kwargs):
        calls.append("valid")
        return json.dumps({"operations": [{"action": "edit", "item_id": target["id"],
            "changes": {"duration_min": 0, "note": "确认后更新"}}]})

    monkeypatch.setattr(trip_ai, "chat", valid_chat)
    preview = c.post(path, headers=owner, json=payload)
    assert preview.status_code == 201, preview.text
    assert calls == ["invalid", "invalid", "valid"]
    assert c.get(f"/api/trips/{trip['id']}", headers=owner).json() == trip
    proposal = preview.json()
    apply = f"{path}/{proposal['id']}/apply"
    assert c.post(apply, headers=stranger, json={"version": trip["version"]}).status_code == 404
    applied = c.post(apply, headers=owner, json={"version": trip["version"]})
    assert applied.status_code == 200, applied.text
    result = applied.json()
    expected = {**target, "duration_min": 0, "note": "确认后更新"}
    assert result["days"][0]["items"] == [expected, trip["days"][0]["items"][1]]
    assert result["version"] == trip["version"] + 1
    assert c.post(apply, headers=owner, json={"version": trip["version"]}).status_code == 409


def test_background_invalid_reply_is_terminal_and_explicit_retry_retains_preview(client, monkeypatch):
    c, sessions = client
    owner = account(c, "schema_job_owner")
    trip = setup_trip(c, owner)
    initial = trip["days"][0]["items"][0]
    update = {key: initial[key] for key in (
        "name", "kind", "start_time", "note", "activity", "address", "lng", "lat", "amap_id", "source")}
    seeded = c.patch(f"/api/trips/{trip['id']}/items/{initial['id']}", headers=owner,
        json={**update, "duration_min": 30, "version": trip["version"]})
    assert seeded.status_code == 200, seeded.text
    trip = seeded.json()
    path = f"/api/trips/{trip['id']}/ai-jobs"
    payload = {"version": trip["version"], "request": "清空第一项时长"}
    target = trip["days"][0]["items"][0]
    calls = []

    async def invalid_chat(*args, **kwargs):
        calls.append("invalid")
        return json.dumps({"operations": [{"action": "edit", "item_id": target["id"],
            "changes": {"duration_min": True}}]})

    monkeypatch.setattr(trip_ai, "chat", invalid_chat)
    assert c.post(path, headers=owner, json=payload).status_code == 202
    failed = wait_for_job(c, path, owner, "error")
    assert failed["proposal"] is None and failed["error"]
    assert calls == ["invalid", "invalid"]
    assert c.get(f"/api/trips/{trip['id']}", headers=owner).json() == trip
    with sessions() as db:
        assert db.query(TripProposal).count() == 0
        job = db.query(TripAiJob).one()
        assert job.status == "error" and job.snapshot_json == ""
        assert job.lease_token == "" and job.lease_until is None

    async def valid_chat(*args, **kwargs):
        calls.append("valid")
        return json.dumps({"operations": [{"action": "edit", "item_id": target["id"],
            "changes": {"duration_min": None}}]})

    monkeypatch.setattr(trip_ai, "chat", valid_chat)
    assert c.post(path, headers=owner, json=payload).status_code == 202
    done = wait_for_job(c, path, owner, "done")
    proposal = done["proposal"]
    assert proposal is not None and proposal["base_version"] == trip["version"]
    assert calls == ["invalid", "invalid", "valid"]
    assert c.get(f"/api/trips/{trip['id']}", headers=owner).json() == trip
    # A fresh HTTP request recovers the saved preview; it does not run the model.
    assert c.get(path + "/latest", headers=owner).json()["job"] == done
    with sessions() as db:
        assert db.query(TripProposal).count() == 1
        assert db.query(TripAiJob).one().status == "done"
    result = c.post(f"/api/trips/{trip['id']}/ai-proposals/{proposal['id']}/apply",
        headers=owner, json={"version": trip["version"]})
    assert result.status_code == 200, result.text
    changed = result.json()["days"][0]["items"][0]
    assert changed == {**target, "duration_min": None}
    assert result.json()["version"] == trip["version"] + 1
