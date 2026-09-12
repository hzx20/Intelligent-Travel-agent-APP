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
from fastapi import FastAPI

from app.db.database import init_db
from app.routers.admin import router as admin_router
from app.routers.auth import router as auth_router
from app.routers.guides import router as guides_router
from app.routers.hotels import router as hotels_router
from app.routers.map import router as map_router
from app.routers.me import router as me_router
from app.routers.planner import router as planner_router
from app.routers.spots import router as spots_router
from app.schemas import ClarifyRequest, ClarifyResponse, ItineraryRequest
from app.services.clarify import clarify_turn
from app.services.itinerary import generate_itinerary

app = FastAPI(title="智能旅行规划 Agent API", version="0.14.0")

# v0.5：启动时建库建表（幂等），并挂载用户系统路由
init_db()
app.include_router(auth_router)
app.include_router(spots_router)
app.include_router(guides_router)
app.include_router(map_router)
app.include_router(hotels_router)
app.include_router(me_router)
app.include_router(admin_router)
app.include_router(planner_router)


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
