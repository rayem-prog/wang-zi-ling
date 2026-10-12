"""HTTP parameters and routes for selection and the personal watchlist."""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import JSONResponse
from services.result_store import ResultStoreError
from config.settings import load_settings
from services import signals

router = APIRouter(prefix="/api/signals", tags=["signals"])


@router.post('/refresh')
def refresh_signals() -> dict[str, Any]:
    from api.jobs import daily_jobs, JobConflict
    try:
        return daily_jobs.start(True, send_notification=False)
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get('/snapshot')
def get_signals_snapshot(
    bracket: str = Query('all'), min_price: float | None = Query(None, gt=0, allow_inf_nan=False),
    max_price: float | None = Query(None, gt=0, allow_inf_nan=False), sort_by: str = Query('score'),
    codes: str | None = Query(None), q: str = Query('', max_length=200),
):
    try:
        body = signals.get_snapshot(load_settings(), filters=dict(bracket=bracket, min_price=min_price,
            max_price=max_price, sort_by=sort_by, codes=codes, q=q))
        return JSONResponse(body, headers={'Cache-Control': 'no-store'})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ResultStoreError:
        return JSONResponse(status_code=503, content={'error': {'code': 'result_store_unavailable',
            'message': '结果库暂时不可读取，请稍后重试', 'retryable': True}}, headers={'Cache-Control': 'no-store'})


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
