"""Bounded JSON transport for the fixed travel-data suppliers; never retries paid calls."""
import json

import httpx


class SupplierError(RuntimeError):
    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code


def request_json(client, method, url, *, max_bytes=1024 * 1024, **kwargs):
    try:
        with client.stream(method, url, **kwargs) as response:
            if response.status_code in (401, 403):
                raise SupplierError("供应商拒绝访问，请检查服务端凭据、接口权限及余额")
            if response.status_code == 429:
                raise SupplierError("供应商请求过于频繁，请稍后查询", 429)
            if not 200 <= response.status_code < 300:
                raise SupplierError("供应商暂不可用，请稍后查询")
            parts, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise SupplierError("供应商返回数据过大，请缩小查询范围")
                parts.append(chunk)
            data = json.loads(b"".join(parts))
            if not isinstance(data, dict):
                raise SupplierError("供应商返回格式异常")
            return data
    except (httpx.HTTPError, ValueError, UnicodeError):
        # Exception text may contain credentials or upstream response bodies.
        raise SupplierError("供应商连接失败或返回格式异常，请稍后查询") from None
