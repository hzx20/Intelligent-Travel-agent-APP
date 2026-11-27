"""FastAPI 应用入口。

对应关系：Express 的 app.js 路由 → FastAPI 的 main.py 路由
接口契约与 Node 版约定保持不变：
- GET  /api/health    健康检查
- POST /api/clarify   M1 需求澄清
- POST /api/itinerary M2 行程生成

额外福利：启动后访问 /docs 可看到自动生成的接口文档（FastAPI 内置，
浏览器打开就是可交互的调试页，这是 Express 做不到的）。

启动方式（在项目根目录）：
  backend\\.venv 运行说明见项目 README；实际命令：
  uvicorn app.main:app --reload --port 3000 --app-dir backend
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core import metrics
from app.db.database import init_db
from app.routers.admin import router as admin_router
from app.routers.auth import router as auth_router
from app.routers.channel_inventory import router as channel_inventory_router
from app.routers.travel_data import router as travel_data_router
from app.routers.guides import router as guides_router
from app.routers.hotels import router as hotels_router
from app.routers.map import router as map_router
from app.routers.me import router as me_router
from app.routers.metrics import router as metrics_router
from app.routers.planner import router as planner_router
from app.routers.spots import router as spots_router
from app.routers.trips import router as trips_router
from app.routers import trips as trips_module
from app.routers.trip_expenses import router as trip_expenses_router
from app.routers.trip_imports import router as trip_imports_router
from app.routers.trip_guest_import import router as trip_guest_import_router
from app.routers.trip_plan_import import router as trip_plan_import_router
from app.routers.trip_sharing import router as trip_sharing_router
from app.routers.trip_footprints import router as trip_footprints_router
from app.routers.trip_reservations import router as trip_reservations_router
from app.routers.usage import router as usage_router
from app.schemas import ClarifyRequest, ClarifyResponse, ItineraryRequest
from app.services.clarify import clarify_turn
from app.services.itinerary import generate_itinerary
from app.services import plan_jobs

@asynccontextmanager
async def lifespan(app):
    trips_module.start_ai_scheduler()
    plan_jobs.start_scheduler()
    try:
        yield
    finally:
        await plan_jobs.stop_scheduler()
        await trips_module.stop_ai_scheduler()


app = FastAPI(title="智能旅行规划 Agent API", version="0.15.0", lifespan=lifespan)

# One local data volume shared by all workers; disabled unless explicitly enabled.
import os
if os.environ.get('TRAVEL_SHARED_LIMITS', '').lower() in ('1', 'true', 'yes'):
    from app.core.shared_limits import SharedPublicLimits, SharedQuota
    from app.db.database import DATA_DIR
    app.add_middleware(SharedPublicLimits, store=SharedQuota(DATA_DIR / 'request-quotas.db'))


# v0.5：启动时建库建表（幂等），并挂载用户系统路由
init_db()
app.include_router(auth_router)
app.include_router(spots_router)
app.include_router(guides_router)
app.include_router(map_router)
app.include_router(hotels_router)
app.include_router(me_router)
app.include_router(admin_router)
app.include_router(channel_inventory_router)
app.include_router(travel_data_router)
app.include_router(planner_router)
app.include_router(trip_guest_import_router)
app.include_router(trip_plan_import_router)
app.include_router(trips_router)
app.include_router(trip_expenses_router)
app.include_router(trip_imports_router)
app.include_router(trip_sharing_router)
app.include_router(trip_footprints_router)
app.include_router(trip_reservations_router)
app.include_router(usage_router)
app.include_router(metrics_router)


# 请求量指标：用纯 ASGI 中间件而不是 @app.middleware("http")。
# 原因：BaseHTTPMiddleware 会把每个请求包进 anyio 任务组，对 SSE 流式响应
# （/api/trips/*/plan-stream）的生命周期有干扰——实测会让后台规划任务拿到
# 已关闭的事件循环（Event loop is closed）。纯 ASGI 只在响应开始消息里读一次
# scope["route"] 计数，不创建任务、不缓冲响应体，流式行为与无中间件时完全一致。
class RequestMetricsMiddleware:
    """计数 (方法, 路由模板, 状态码) 的纯 ASGI 中间件。

    用路由模板而不是原始路径：/api/trips/123 与 /api/trips/456 应聚合成
    /api/trips/{trip_id}，否则每个 trip id 都会撑出一个新时间序列。
    未匹配到路由的请求（404）记作 "unmatched"。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                try:
                    route = scope.get("route")
                    path = getattr(route, "path", None) or "unmatched"
                    metrics.REQUESTS.labels(
                        method=str(scope.get("method", "?")),
                        path=path,
                        status=str(message.get("status", "?")),
                    ).inc()
                except Exception:
                    pass  # 指标挂了不影响业务响应
            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(RequestMetricsMiddleware)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/clarify", response_model=ClarifyResponse)
async def api_clarify(req: ClarifyRequest):
    result = await clarify_turn(
        [m.model_dump() for m in req.history],
        req.user_input,
        req.prev_collected,
    )
    return result


@app.post("/api/itinerary")
async def api_itinerary(req: ItineraryRequest):
    return await generate_itinerary(req.collected)
