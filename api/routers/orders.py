"""次日操作指令单 API。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.get("")
def get_orders() -> list[dict[str, Any]]:
    orders_path = Path("artifacts/latest_orders.csv")
    if not orders_path.exists():
        return []

    try:
        df = pd.read_csv(orders_path, dtype={"code": str})
        return df.fillna("").to_dict(orient="records")
    except Exception:
        return []


@router.get("/export")
def export_orders_csv():
    orders_path = Path("artifacts/latest_orders.csv")
    if not orders_path.exists():
        raise HTTPException(status_code=404, detail="指令单文件不存在")

    content = orders_path.read_bytes()
    filename = f"stockpilot_orders_{dt.date.today()}.csv"
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
