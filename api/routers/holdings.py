"""实盘持仓诊断、模拟盘账本 API。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config.settings import load_settings
from data import db
from data.health import assess_data_health, recommendation_state
from strategy import holdings, paper
from data.timeliness import quote_state
from api.health_cache import health_cache
from services.holding_valuation import valuation_quotes
from services.decision_results import operation_frame, decision_context, DecisionUnavailable, require_candidate, require_snapshot_reference

router = APIRouter(prefix="/api/holdings", tags=["holdings"])


class PositionItem(BaseModel):
    code: str
    name: str
    shares: float
    cost: float


class PaperOrderItem(BaseModel):
    snapshot_id: str | None = None
    code: str
    shares: float
    price: float
    side: str  # "买入" 或 "卖出"


@router.get("")
def get_holdings_and_diagnostics() -> dict[str, Any]:
    settings = load_settings()
    try:
        pos_df = holdings.load_positions(settings.data.db_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"持仓读取失败: {exc}") from exc

    health = health_cache.get(settings)
    rec_state = recommendation_state(settings, health)
    rec = operation_frame(settings, rec_state)

    spot = db.load_spot(settings.data.db_path)
    spot_updated_at = str(spot["ts"].max()) if not spot.empty and "ts" in spot else ""
    spot, states, valuation_pending = valuation_quotes(settings, pos_df, spot)
    stale_codes = sorted(str(c) for c in pos_df.code if states.get(str(c), {'stale': True})['stale'])
    spot_stale = bool(stale_codes)
    if not spot.empty:
        spot = spot[spot.code.astype(str).map(lambda c: not states[c]['stale'])].copy()
    else:
        spot = pd.DataFrame(columns=['code','price'])

    diag_df = holdings.diagnose_positions(settings.data.db_path, spot, rec, settings)
    if not diag_df.empty:
        for key in ('trade_date', 'quote_time', 'price_source', 'price_basis', 'reason'):
            if key == 'reason':
                diag_df['price_reason'] = diag_df.code.astype(str).map(lambda c: states.get(c, {}).get(key))
                continue
            diag_df[key] = diag_df.code.astype(str).map(lambda c: states.get(c, {}).get(key))
        diag_df['price_stale'] = diag_df.code.astype(str).map(lambda c: states.get(c, {'stale': True})['stale'])
    warnings = holdings.portfolio_warnings(settings.data.db_path, spot, rec, settings)

    return {
        "positions": pos_df.to_dict(orient="records") if not pos_df.empty else [],
        "diagnostics": (diag_df.astype(object).where(pd.notna(diag_df), None).to_dict(orient="records")
                        if not diag_df.empty else []),
        "warnings": ([f"{c} 盈亏暂不可用：{states.get(c, {}).get('reason', '缺少可信行情')}" for c in stale_codes]) + warnings,
        "valuation_pending": valuation_pending,
        "valuation_message": "；".join(f"截至 {date} 收盘估算盈亏（未复权收盘价）" for date in sorted({s['trade_date'] for s in states.values() if s.get('price_basis') == 'close'})),
        "recommendation_reason": "" if rec_state["ready"] else rec_state["reason"],
        "spot_updated_at": spot_updated_at,
        "spot_stale": spot_stale,
        "stale_codes": stale_codes,
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
    summary = paper.paper_summary(str(Path(settings.paths.artifact_dir) / "paper.db"), prices)
    return summary


@router.post("/paper")
def create_paper_order(order: PaperOrderItem) -> dict[str, Any]:
    settings = load_settings()
    db_path = str(Path(settings.paths.artifact_dir) / "paper.db")
    if order.side.upper() in ('BUY', '买入'):
        try:
            require_snapshot_reference(settings, order.snapshot_id)
        except DecisionUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    if order.snapshot_id and order.side.upper() in ('BUY', '买入'):
        try:
            with decision_context(settings, assess_data_health(settings), expected_snapshot_id=order.snapshot_id, for_new_buy=True) as snapshot:
                require_candidate(snapshot, order.code)
                paper.execute_paper_order(db_path, order.code, order.code, order.side, order.shares, order.price)
            return {"status": "success", "order": order.model_dump()}
        except DecisionUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        paper.execute_paper_order(db_path, order.code, order.code, order.side, order.shares, order.price)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "order": order.model_dump()}
