"""宏观温度计与风险闸门 API。"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from config.settings import load_settings
from data import db
from strategy import macro

router = APIRouter(prefix="/api/macro", tags=["macro"])


@router.get("")
def get_macro_overview() -> dict[str, Any]:
    settings = load_settings()
    macro_df = db.load_macro_history(settings.data.db_path, limit=30)

    latest_data = {}
    history_list = []
    if not macro_df.empty:
        history_list = macro_df.to_dict(orient="records")
        top = history_list[0]
        details_str = top.get("details", "{}")
        try:
            details_obj = json.loads(details_str) if isinstance(details_str, str) else details_str
        except Exception:
            details_obj = {}

        stance = str(top.get("stance", "NEUTRAL")).upper()
        cap = 0.8 if stance == "ATTACK" else (0.3 if stance == "DEFENSE" else 0.6)
        latest_data = {
            "date": str(top.get("date", "")),
            "score": float(top.get("score", 0.0)),
            "stance": stance,
            "trend": float(top.get("trend", 0.0)),
            "breadth": float(top.get("breadth", 0.0)),
            "vol": float(top.get("vol", 0.0)),
            "cap": cap,
            "allow_new_buy": stance != "DEFENSE",
            "details": details_obj,
        }
    else:
        latest_data = {
            "date": str(dt.date.today()),
            "score": 0.0,
            "stance": "NEUTRAL",
            "trend": 0.0,
            "breadth": 0.0,
            "vol": 0.0,
            "cap": 0.6,
            "allow_new_buy": True,
            "details": {},
        }

    # 检查日历事件
    calendar_path = Path("config/events.yaml")
    is_event = False
    event_names = []
    if calendar_path.exists():
        try:
            is_event, event_names = macro.is_near_event(dt.date.today(), str(calendar_path))
        except Exception:
            pass

    return {
        "current": latest_data,
        "event_status": {
            "is_near_event": is_event,
            "event_names": event_names,
        },
        "history": history_list,
    }
