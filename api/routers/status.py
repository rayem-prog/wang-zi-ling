"""系统状态与心跳监控 API。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from api.ws import get_market_trading_status
from config.settings import load_settings
from data import db

router = APIRouter(prefix="/api/status", tags=["status"])


def evaluate_daemon_status(hb: dict | None, max_delay_seconds: int = 180) -> dict[str, Any]:
    if not hb:
        return {
            "online": False,
            "status": "offline",
            "title": "离线未运行",
            "detail": "暂无心跳记录",
            "pid": 0,
            "timestamp": "",
        }

    ts_str = hb.get("timestamp", "")
    status = hb.get("status", "unknown")
    pid = hb.get("pid", 0)
    msg = hb.get("message", "")

    try:
        ts = dt.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        now_utc = dt.datetime.now(dt.timezone.utc)
        elapsed = (now_utc - ts).total_seconds()
    except Exception:
        elapsed = 9999

    is_online = (elapsed <= max_delay_seconds) and (status in ("running", "started", "standby"))
    return {
        "online": is_online,
        "status": status,
        "title": f"运行中 (PID: {pid})" if is_online else f"异常/离线 (PID: {pid})",
        "detail": f"最近心跳: {ts_str} ({int(elapsed)}秒前) | {msg}",
        "pid": pid,
        "timestamp": ts_str,
        "elapsed_seconds": int(elapsed),
    }


@router.get("")
def get_system_status() -> dict[str, Any]:
    settings = load_settings()
    try:
        hb = db.get_latest_heartbeat(settings.data.db_path)
    except Exception:
        db.init_db(settings.data.db_path)
        hb = db.get_latest_heartbeat(settings.data.db_path)
    daemon_info = evaluate_daemon_status(hb)

    # 宏观信息
    try:
        macro_df = db.load_macro_history(settings.data.db_path, limit=1)
    except Exception:
        macro_df = db.pd.DataFrame() if hasattr(db, "pd") else None
    if not macro_df.empty:
        latest_macro = macro_df.iloc[0].to_dict()
        stance = str(latest_macro.get("stance", "NEUTRAL")).upper()
        cap = 0.8 if stance == "ATTACK" else (0.3 if stance == "DEFENSE" else 0.6)
        allow_buy = stance != "DEFENSE"
        macro_info = {
            "score": float(latest_macro.get("score", 0.0)),
            "stance": stance,
            "cap": cap,
            "allow_new_buy": allow_buy,
            "date": str(latest_macro.get("date", "")),
        }
    else:
        macro_info = {
            "score": 0.0,
            "stance": "NEUTRAL",
            "cap": 0.6,
            "allow_new_buy": True,
            "date": "",
        }

    # 文件统计
    rec_count = 0
    rec_p = Path("artifacts/recommendations.csv")
    if rec_p.exists():
        try:
            import pandas as pd
            rec_count = len(pd.read_csv(rec_p))
        except Exception:
            pass

    orders_count = 0
    ord_p = Path("artifacts/latest_orders.csv")
    if ord_p.exists():
        try:
            import pandas as pd
            orders_count = len(pd.read_csv(ord_p))
        except Exception:
            pass

    return {
        "server_time": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market": get_market_trading_status(),
        "daemon": daemon_info,
        "macro": macro_info,
        "counts": {
            "recommendations": rec_count,
            "orders": orders_count,
        },
    }
