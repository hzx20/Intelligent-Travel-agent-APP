"""Explicit read-only calls to VariFlight's documented stateless JSON MCP endpoint."""
from datetime import date, datetime, timezone
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import settings
from app.core.provider_budget import ProviderBusy, provider_slot
from app.services.supplier_http import SupplierError, request_json

ENDPOINT = "https://ai.variflight.com/servers/aviation/mcp"


class FlightQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["number", "route", "prices"] = "number"
    date: date
    flight_number: str = Field(default="", max_length=8, pattern=r"^(?:[A-Z0-9]{2}[0-9]{3,4}[A-Z]?)?$")
    origin: str = Field(default="", pattern=r"^(?:[A-Z]{3})?$")
    destination: str = Field(default="", pattern=r"^(?:[A-Z]{3})?$")

    @model_validator(mode="after")
    def required_arguments(self):
        if self.kind == "number":
            if not self.flight_number or self.origin or self.destination:
                raise ValueError("按航班号查询时请填写航班号，并清空城市代码")
        elif not self.origin or not self.destination or self.origin == self.destination or self.flight_number:
            raise ValueError("请填写不同的出发、到达城市 IATA 代码，并清空航班号")
        return self

    def tool_call(self):
        day = self.date.isoformat()
        if self.kind == "number":
            return "searchFlightsByNumber", {"date": day, "fnum": self.flight_number}
        if self.kind == "route":
            return "searchFlightsByDepArr", {"date": day, "depcity": self.origin,
                    "arrcity": self.destination, "limit": 20, "offset": 0, "detail": "summary"}
        return "getFlightPriceByCities", {"dep_date": day, "dep_city": self.origin, "arr_city": self.destination}


def query_flights(query: FlightQuery, *, client=None):
    if not settings.variflight_api_key:
        raise SupplierError("飞常准尚未配置 API key", 503)
    tool, arguments = query.tool_call()
    headers = {"X-API-Key": settings.variflight_api_key,
               "MCP-Protocol-Version": "2025-03-26", "Accept": "application/json, text/event-stream"}
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": tool, "arguments": arguments}}
    # This supplier documents direct JSON-RPC against its stateless HTTP service.
    # One explicit query = one tools/call; no startup discovery or automatic retries.
    try:
        with provider_slot("variflight"):
            if client is None:
                with httpx.Client(timeout=httpx.Timeout(35, connect=5), trust_env=False, follow_redirects=False) as connection:
                    data = request_json(connection, "POST", ENDPOINT, headers=headers, json=payload)
            else:
                data = request_json(client, "POST", ENDPOINT, headers=headers, json=payload)
    except ProviderBusy:
        raise SupplierError("航班查询繁忙，请稍后查询", 429) from None
    if data.get("jsonrpc") != "2.0" or data.get("id") != 1 or "error" in data:
        raise SupplierError("飞常准查询失败，请检查凭据、权限、余额和查询条件")
    result = data.get("result")
    if not isinstance(result, dict) or result.get("isError"):
        raise SupplierError("飞常准未能完成查询，请检查日期及城市代码")
    content = result.get("content", [])
    if not isinstance(content, list):
        raise SupplierError("飞常准返回格式异常")
    texts = [c["text"] for c in content if isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str)]
    if any(text.lstrip().lower().startswith("error executing tool") for text in texts):
        raise SupplierError("飞常准未能完成查询，请检查日期、城市代码及服务权限")
    structured = result.get("structuredContent")
    if not texts and not isinstance(structured, dict):
        raise SupplierError("飞常准返回内容为空")
    return {"provider": "variflight", "source": "飞常准", "tool": tool, "texts": texts,
            "structured": structured if isinstance(structured, dict) else None,
            "queried_at": datetime.now(timezone.utc).isoformat()}
