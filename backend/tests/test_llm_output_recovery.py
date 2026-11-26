"""Model-output recovery is deterministic and does not call external services."""
import asyncio
import json

import pytest

from app.services import itinerary


def _reply(days):
    for day in days:
        if isinstance(day, dict):
            for item in day.get('items', []):
                if isinstance(item, dict):
                    item.setdefault('cost_category', '其他')
    return json.dumps({"days": days, "budget_format": "items_and_additions_v1", "additional_costs": []}, ensure_ascii=False)


def _day(name="西湖"):
    return {"items": [{"poi": name, "time": "09:00"}]}


def test_invalid_json_is_repaired_once(monkeypatch):
    responses = iter(["not json", _reply([_day()])])
    requests = []

    async def fake_chat(messages, **kwargs):
        requests.append(messages)
        return next(responses)

    monkeypatch.setattr(itinerary, "chat", fake_chat)
    result = asyncio.run(itinerary.generate_itinerary({"destination": "杭州", "days": 1}))
    assert result["days"][0]["spots"][0]["name"] == "西湖"
    assert len(requests) == 2
    assert "不是有效的 JSON" in requests[1][1]["content"]


def test_retry_output_is_validated_before_acceptance(monkeypatch):
    responses = iter([_reply([_day()]), _reply([_day(), {"items": []}])])

    async def fake_chat(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr(itinerary, "chat", fake_chat)
    with pytest.raises(RuntimeError, match="第 2 天没有安排"):
        asyncio.run(itinerary.generate_itinerary({"destination": "杭州", "days": 2}))


def test_empty_location_and_bad_day_shape_are_rejected(monkeypatch):
    responses = iter([_reply([_day("")]), _reply([None])])

    async def fake_chat(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr(itinerary, "chat", fake_chat)
    with pytest.raises(RuntimeError, match="第 1 天的结构无效"):
        asyncio.run(itinerary.generate_itinerary({"days": 1}))


def test_provider_failure_is_not_retried_as_output_error(monkeypatch):
    calls = 0

    async def fake_chat(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(itinerary, "chat", fake_chat)
    with pytest.raises(RuntimeError, match="provider unavailable"):
        asyncio.run(itinerary.generate_itinerary({"days": 1}))
    assert calls == 1
