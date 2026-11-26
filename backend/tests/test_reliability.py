"""无需外部服务的关键边界回归。"""
import asyncio
import json

import jwt

from app import security
from app.services import clarify


def test_clarification_checks_required_fields_even_if_model_omits_missing(monkeypatch):
    async def fake_chat(*args, **kwargs):
        return json.dumps({"collected": {"destination": "成都"}, "missing": []})

    monkeypatch.setattr(clarify, "chat", fake_chat)
    result = asyncio.run(clarify.clarify_turn([], "去成都"))
    assert result["done"] is False
    assert result["missing"] == ["days"]
    assert result["question"]


def test_signed_token_without_subject_is_rejected():
    token = jwt.encode({}, security._jwt_secret(), algorithm="HS256")
    assert security.decode_token(token) is None
