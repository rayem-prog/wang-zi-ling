"""次日操作指令单 API。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from config.settings import load_settings
from strategy import risk
from services.decision_results import decision_context, DecisionUnavailable, operation_frame
from data.health import assess_data_health, recommendation_state

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.get("")
def get_orders() -> list[dict[str, Any]]:
    settings = load_settings()
    state = recommendation_state(settings, assess_data_health(settings))
    if not state["ready"]:
        return []
    try:
        df = operation_frame(settings, state, 'orders')
        if df.empty:
            return []
        if not risk.buying_allowed(risk.current_policy(settings)):
            df = df[~df["action"].isin(["买入", "加仓"])]
        return df.fillna("").to_dict(orient="records")
    except Exception:
        return []


@router.get("/export")
def export_orders_csv(snapshot_id: str | None = None):
    settings = load_settings()
    try:
        with decision_context(settings, assess_data_health(settings), expected_snapshot_id=snapshot_id) as snapshot:
            if snapshot is not None:
                df = pd.DataFrame(snapshot.get('orders', []))
            else:
                path = Path(settings.paths.artifact_dir) / 'latest_orders.csv'
                if not path.exists():
                    raise HTTPException(status_code=404, detail="指令单文件不存在")
                df = pd.read_csv(path, dtype={"code": str})
            if not df.empty and not risk.buying_allowed(risk.current_policy(settings)):
                df = df[~df["action"].isin(["买入", "加仓"])]
            content = df.to_csv(index=False).encode("utf-8-sig")
    except DecisionUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    filename = f"stockpilot_orders_{dt.date.today()}.csv"
    return Response(content=content, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
