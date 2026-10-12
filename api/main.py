"""StockPilot v2 FastAPI 后端服务主入口。"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.routers import holdings, intraday, kline, macro, orders, paper, recap, sandbox, search, settings, signals, status
from api.ws import get_market_trading_status, manager
from config.settings import load_settings
from data import db

logger = logging.getLogger("stockpilot.api")


async def _background_push_loop() -> None:
    """盘中与盘外周期性向 WebSocket 推送状态与行情心跳。"""
    while True:
        try:
            if manager.active_connections:
                status_res = await run_in_threadpool(status.get_system_status)
                payload = {
                    "type": "heartbeat",
                    "timestamp": dt.datetime.now().isoformat(),
                    "data": status_res,
                }
                await manager.broadcast(payload)
        except Exception as e:
            logger.debug("Background push error: %s", e)

        is_trading = get_market_trading_status()["is_trading"]
        await asyncio.sleep(2 if is_trading else 10)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    db.init_db(settings.data.db_path)
    push_task = asyncio.create_task(_background_push_loop())
    from api.market_refresh import refresh_loop
    refresh_task = asyncio.create_task(refresh_loop())
    logger.info("StockPilot API Server started.")
    yield
    refresh_task.cancel()
    try:
        await refresh_task
    except asyncio.CancelledError:
        pass
    push_task.cancel()
    try:
        await push_task
    except asyncio.CancelledError:
        pass
    logger.info("StockPilot API Server shutdown.")


app = FastAPI(
    title="StockPilot v2 API",
    description="StockPilot 量化交易与盘中风控统一 API 后端",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册 API 路由
app.include_router(status.router)
app.include_router(signals.router)
app.include_router(orders.router)
app.include_router(macro.router)
app.include_router(holdings.router)
app.include_router(recap.router)
app.include_router(kline.router)
app.include_router(settings.router)
app.include_router(search.router)
app.include_router(intraday.router)
app.include_router(paper.router)
app.include_router(sandbox.router)


@app.websocket("/ws/stream")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        init_status = await run_in_threadpool(status.get_system_status)
        await websocket.send_json({"type": "init", "data": init_status})
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        root = FRONTEND_DIST.resolve()
        file_path = (root / full_path).resolve()
        if not file_path.is_relative_to(root):
            raise HTTPException(status_code=404, detail="文件不存在")
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        index = (root / "index.html").resolve()
        if not index.is_relative_to(root) or not index.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")
        return FileResponse(index)
else:
    @app.get("/")
    def index_placeholder():
        return {
            "message": "StockPilot v2 API is running. Frontend dist not found yet. Run 'npm run build' in frontend/.",
            "status_api": "/api/status",
        }
