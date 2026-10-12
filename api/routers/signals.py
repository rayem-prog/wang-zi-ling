"""HTTP parameters and routes for selection and the personal watchlist."""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Query
from config.settings import load_settings
from services import signals

router = APIRouter(prefix="/api/signals", tags=["signals"])


@router.post('/refresh')
def refresh_signals() -> dict[str, Any]:
    from api.jobs import daily_jobs
    return daily_jobs.start(True, send_notification=False)


@router.get("")
def get_signals(
    bracket: str = Query("all", description="价格区间筛选: all, low, mid, high, top"),
    min_price: float | None = Query(None, description="最低股价"),
    max_price: float | None = Query(None, description="最高股价"),
    sort_by: str = Query("score", description="排序规则: score, price_asc, price_desc, hand_cost_asc"),
    codes: str | None = Query(None, description="指定代码筛选 (逗号分隔)"),
) -> list[dict[str, Any]]:
    return signals.list_signals(load_settings(), bracket=bracket, min_price=min_price,
                                max_price=max_price, sort_by=sort_by, codes=codes)


@router.get("/watchlist")
def get_watchlist() -> list[dict[str, Any]]:
    return signals.list_watchlist(load_settings())


@router.post("/watchlist")
def add_to_watchlist(payload: dict[str, str]) -> dict[str, Any]:
    return signals.add_to_watchlist(load_settings(), payload)


@router.delete("/watchlist/{code}")
def remove_from_watchlist(code: str) -> dict[str, Any]:
    return signals.remove_from_watchlist(load_settings(), code)


@router.get("/stats")
def get_signals_stats() -> dict[str, Any]:
    return signals.signal_stats(load_settings())
