"""静态地图（v0.7 收尾）：后端代理高德静态地图，返回一个图片。

为什么走后端代理而不是前端直接拼 URL？
    静态地图的请求里必须带上高德的 key。前端拼 URL 等于把 key 写进网页源码，
    谁打开 F12 都能抄走、刷我们的调用配额。由后端代拿再转发图片，key 只留在服务器。

为什么是"静态"地图？
    可拖可缩放的 JS 地图要"Web端(JS API)"类型的 key，和现在这个"Web 服务"key
    不是同一张，得另外申请开通。静态地图属于 Web 服务，现有 key 直接用，
    代价是不能拖动缩放——换来看一眼就懂的路线全貌，够用。
"""
import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.core.config import settings

router = APIRouter(prefix="/api/map", tags=["map"])

STATIC_URL = "https://restapi.amap.com/v3/staticmap"
LABELS = "ABCDEFGHIJKLMNOPQRST"  # 静态地图标记只支持单字符编号
MAX_POINTS = 20

# 简单内存缓存：同样参数只下载一次（图片小、变化少，进程重启即失效）
_cache: dict[str, bytes] = {}
CACHE_MAX = 80


def _parse_points(raw: str) -> list[tuple[float, float]]:
    """解析 "lng,lat;lng,lat" → [(lng, lat)]，脏数据自动丢弃。"""
    pts: list[tuple[float, float]] = []
    for seg in (raw or "").split(";"):
        parts = seg.strip().split(",")
        if len(parts) != 2:
            continue
        try:
            pts.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    return pts[:MAX_POINTS]


@router.get("/static")
def static_map(
    points: str = Query(..., description="坐标串：lng,lat;lng,lat（最多 20 点）"),
    size: str = Query("640*320", description="图片尺寸，最大 1024*1024"),
    zoom: int = Query(11, ge=1, le=17),
    label: int = Query(1, ge=0, le=1, description="是否在标记上显示 A/B/C 编号"),
    path: int = Query(1, ge=0, le=1, description="是否按点位顺序画连线"),
):
    """生成行程路线图（PNG）。失败返回 502，前端会自动隐藏图片。"""
    pts = _parse_points(points)
    if not pts:
        raise HTTPException(400, "缺少有效坐标")

    coord = ";".join(f"{lng},{lat}" for lng, lat in pts)
    style = f"mid,0x1a6e50,{LABELS[0] if label else ''}"  # 绿标记，可选编号
    params = {
        "key": settings.amap_api_key,
        "size": size,
        "zoom": zoom,
        "markers": f"{style}:{coord}",
    }
    if path and len(pts) > 1:
        # 连线：粗细 4，绿色，半透明
        params["paths"] = f"4,0x1a6e50,0.7,,:{coord}"

    cache_key = f"{size}|{zoom}|{label}|{path}|{coord}"
    if cache_key in _cache:
        return Response(_cache[cache_key], media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})

    try:
        resp = httpx.get(STATIC_URL, params=params, timeout=12)
    except httpx.HTTPError:
        raise HTTPException(502, "地图服务暂不可用")

    # 高德出错时返回 JSON（不是图片），这里识别出来转成明确的失败
    ctype = resp.headers.get("content-type", "")
    if resp.status_code != 200 or "image" not in ctype:
        raise HTTPException(502, "地图服务返回异常")

    if len(_cache) >= CACHE_MAX:
        _cache.clear()
    _cache[cache_key] = resp.content
    return Response(resp.content, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})
