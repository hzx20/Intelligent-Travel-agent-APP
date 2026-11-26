"""Explicit live check: one city forecast + free MCP discovery. Outputs no credentials or business rows."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.core.config import settings
from app.services import qweather, variflight
from app.services.supplier_http import SupplierError, request_json


def main():
    report = {}
    try:
        weather = qweather.fetch_weather("杭州")
        report["qweather"] = {"ok": weather["ok"], "forecast_days": len(weather["casts"]),
                              "attribution_present": bool(weather["attributions"])}
    except SupplierError as error:
        report["qweather"] = {"ok": False, "error": str(error)}
    if not settings.variflight_api_key:
        report["variflight"] = {"ok": False, "error": "飞常准 API key 未配置"}
    else:
        try:
            with httpx.Client(timeout=httpx.Timeout(35, connect=5), trust_env=False, follow_redirects=False) as client:
                data = request_json(client, "POST", variflight.ENDPOINT,
                    headers={"X-API-Key": settings.variflight_api_key,
                             "MCP-Protocol-Version": "2025-03-26", "Accept": "application/json, text/event-stream"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            tools = {t["name"]: t for t in data.get("result", {}).get("tools", [])}
            expected = {"searchFlightsByNumber": {"date", "fnum"},
                        "searchFlightsByDepArr": {"date", "depcity", "arrcity", "limit", "offset", "detail"},
                        "getFlightPriceByCities": {"dep_date", "dep_city", "arr_city"}}
            checks = {name: name in tools and params <= set(tools[name].get("inputSchema", {}).get("properties", {}))
                      for name, params in expected.items()}
            report["variflight"] = {"ok": all(checks.values()), "tool_schemas": checks,
                                    "paid_data_queried": False}
        except (SupplierError, ValueError, TypeError, KeyError, AttributeError):
            report["variflight"] = {"ok": False, "error": "飞常准免费工具发现失败，请检查凭据、权限和网络"}
    print(json.dumps(report, ensure_ascii=False))
    return 0 if all(item["ok"] for item in report.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
