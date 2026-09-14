"""StockPilot WebSocket 管理器与实时广播。"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from typing import Set

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


def get_market_trading_status() -> dict:
    """计算当前市场时段状态。"""
    now = dt.datetime.now()
    weekday = now.weekday()
    if weekday >= 5:
        return {"is_trading": False, "phase": "weekend", "label": "周末休市"}

    t = now.time()
    t_0915 = dt.time(9, 15)
    t_0925 = dt.time(9, 25)
    t_0930 = dt.time(9, 30)
    t_1130 = dt.time(11, 30)
    t_1300 = dt.time(13, 0)
    t_1500 = dt.time(15, 0)
    t_1505 = dt.time(15, 5)

    if t < t_0915:
        return {"is_trading": False, "phase": "pre_market", "label": "盘前等待"}
    elif t_0915 <= t < t_0925:
        return {"is_trading": False, "phase": "call_auction", "label": "集合竞价"}
    elif t_0925 <= t <= t_1130:
        return {"is_trading": True, "phase": "morning_trading", "label": "早盘交易中"}
    elif t_1130 < t < t_1300:
        return {"is_trading": False, "phase": "lunch_break", "label": "午间休市"}
    elif t_1300 <= t <= t_1505:
        return {"is_trading": True, "phase": "afternoon_trading", "label": "午盘交易中"}
    else:
        return {"is_trading": False, "phase": "post_close", "label": "已收盘"}


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info("WebSocket client connected. Active: %d", len(self.active_connections))

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.discard(websocket)
        logger.info("WebSocket client disconnected. Active: %d", len(self.active_connections))

    async def broadcast(self, data: dict) -> None:
        if not self.active_connections:
            return
        dead = []
        msg_str = json.dumps(data, ensure_ascii=False)
        for ws in self.active_connections:
            try:
                await ws.send_text(msg_str)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active_connections.discard(ws)


manager = ConnectionManager()
