"""实盘持仓诊断、模拟盘账本 API。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter
from pydantic import BaseModel

from config.settings import load_settings
from data import db
from strategy import holdings, paper

router = APIRouter(prefix="/api/holdings", tags=["holdings"])


class PositionItem(BaseModel):
    code: str
    name: str
    shares: float
    cost: float


class PaperOrderItem(BaseModel):
    code: str
    shares: float
    price: float
    side: str  # "买入" 或 "卖出"


@router.get("")
def get_holdings_and_diagnostics() -> dict[str, Any]:
    settings = load_settings()
    try:
        pos_df = holdings.load_positions(settings.data.db_path)
    except Exception:
        # Table might not exist yet
        pos_df = pd.DataFrame(columns=["code", "name", "shares", "cost", "style"])

    rec_path = Path("artifacts/recommendations.csv")
    rec = pd.read_csv(rec_path, dtype={"code": str}) if rec_path.exists() else pd.DataFrame()

    spot = db.load_spot(settings.data.db_path)
    if spot.empty and not rec.empty and "price" in rec.columns:
        spot = rec[["code", "price"]]

    diag_df = holdings.diagnose_positions(settings.data.db_path, spot, rec, settings)
    warnings = holdings.portfolio_warnings(settings.data.db_path, spot, rec, settings)

    return {
        "positions": pos_df.to_dict(orient="records") if not pos_df.empty else [],
        "diagnostics": diag_df.to_dict(orient="records") if not diag_df.empty else [],
        "warnings": warnings,
    }


@router.post("")
def save_position(item: PositionItem) -> dict[str, Any]:
    settings = load_settings()
    df = pd.DataFrame([item.model_dump()])
    holdings.save_positions(settings.data.db_path, df)
    return {"status": "success", "saved": item.model_dump()}


@router.delete("/{code}")
def delete_position(code: str) -> dict[str, Any]:
    settings = load_settings()
    pos_df = holdings.load_positions(settings.data.db_path)
    if not pos_df.empty:
        pos_df = pos_df[pos_df["code"].astype(str) != str(code)]
        # 重写持仓
        conn = db._connect(settings.data.db_path)
        with conn:
            conn.execute("DELETE FROM positions WHERE code = ?", (str(code),))
    return {"status": "success", "deleted": code}


@router.get("/paper")
def get_paper_summary() -> dict[str, Any]:
    settings = load_settings()
    spot = db.load_spot(settings.data.db_path)
    prices = dict(zip(spot["code"].astype(str), spot["price"])) if not spot.empty else {}
    summary = paper.paper_summary("artifacts/paper.db", prices)
    return summary


@router.post("/paper")
def create_paper_order(order: PaperOrderItem) -> dict[str, Any]:
    db_path = "artifacts/paper.db"
    if order.side == "买入":
        paper.open_paper_order(db_path, order.code, order.shares, order.price)
    else:
        paper.close_paper_order(db_path, order.code, order.shares, order.price)
    return {"status": "success", "order": order.model_dump()}
