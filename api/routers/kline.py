"""真实日线与分钟线图表接口。缺数时返回明确空状态。"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
from fastapi import APIRouter

from config.settings import load_settings
from data import archive, db, fetcher
from data.health import _expected_session
from data.trading_calendar import closure_kind, calendar_verified
from data.timeliness import minute_cache_state, exchange_now

router = APIRouter(prefix="/api/kline", tags=["kline"])


def _calculate_mas(closes: list[float]) -> dict[str, list[float | None]]:
    values = pd.Series(closes)
    return {
        f"ma{window}": [
            round(float(value), 2) if pd.notna(value) else None
            for value in values.rolling(window).mean()
        ]
        for window in (5, 10, 20)
    }


def _response(code: str, bars: pd.DataFrame, *, minute: bool,
              source: str = "unknown", updated_at: str = "", error: str = "",
              now: dt.datetime | None = None) -> dict[str, Any]:
    empty = bars.empty
    trade_date = None if empty else str(bars["date"].astype(str).max())
    now = exchange_now(now)
    expected = _expected_session(now).isoformat()
    state = None
    if minute:
        ordered = bars.sort_values(["date", "time"]) if not empty else bars
        latest_time = str(ordered.iloc[-1]["time"]) if not empty else None
        state = minute_cache_state(trade_date, latest_time, now=now)
    stale = state["stale"] if state else empty or trade_date < expected or not calendar_verified(now.date())
    if not empty and (not calendar_verified(dt.date.fromisoformat(trade_date)) or closure_kind(dt.date.fromisoformat(trade_date))):
        stale = True
    quality = bars.attrs.get("verification_status", "quarantined")
    extras = {"cache_status": state["status"] if state else ("stale" if stale else "current"),
              "verification_status": quality, "quarantined_rows": bars.attrs.get("quarantined_rows", len(bars)),
              "expected_at": state["expected_at"] if state else expected}
    if empty:
        message = f"暂无真实{'分钟' if minute else '日线'}行情"
    elif stale:
        message = state["reason"] if state else f"缓存行情停在 {trade_date}，尚未取得 {expected} 的数据"
    else:
        message = ""
    if quality != "verified" and not empty:
        message = (message + "；" if message else "") + "行情尚未通过来源/复权验证，已隔离于训练数据"
    if error:
        message = f"{message}；更新失败: {error}" if message else f"更新失败: {error}"
    if empty:
        return {
            **extras, "code": code, "available": False, "stale": True, "source": source,
            "trade_date": None, "updated_at": updated_at, "message": message,
            "dates": [], "values": [], "volumes": [],
            "mas": {"ma5": [], "ma10": [], "ma20": []},
        }
    bars = bars.sort_values(["date", "time"] if minute else ["date"])
    labels = (
        [f"{row['date']} {row['time']}" for _, row in bars.iterrows()]
        if minute else bars["date"].astype(str).tolist()
    )
    closes = bars["close"].astype(float).tolist()
    return {
        **extras, "code": code, "available": True, "stale": stale, "source": source,
        "trade_date": trade_date, "updated_at": updated_at, "message": message,
        "dates": labels,
        "values": [
            [round(float(row["open"]), 2), round(float(row["close"]), 2),
             round(float(row["low"]), 2), round(float(row["high"]), 2)]
            for _, row in bars.iterrows()
        ],
        "volumes": [int(value) if pd.notna(value) else 0 for value in bars["volume"]],
        "mas": _calculate_mas(closes),
    }


@router.get("/daily/{code}")
def get_daily_kline(code: str) -> dict[str, Any]:
    settings = load_settings()
    error = ""
    try:
        bars = db.load_bars(settings.data.db_path, code)
    except Exception as exc:
        bars = pd.DataFrame()
        error = f"本地日线缓存读取失败: {exc}"
    latest = str(bars["date"].max()) if not bars.empty else None
    expected = _expected_session(dt.datetime.now()).isoformat()
    if latest is None or latest < expected:
        try:
            start = (
                (dt.date.fromisoformat(latest) + dt.timedelta(days=1)).isoformat()
                if latest else settings.data.start_date
            )
            fetched = fetcher.fetch_daily(code, start=start, end=dt.date.today().isoformat(),
                                          adjust=settings.data.adjust)
            if not fetched.empty:
                db.init_db(settings.data.db_path)
                db.save_bars(settings.data.db_path, code, fetched,
                             source=fetched.attrs.get("source", "unknown"))
                bars = db.load_bars(settings.data.db_path, code)
        except Exception as exc:
            error = f"{error}；{exc}" if error else str(exc)
    meta = db.get_latest_bar_metadata(settings.data.db_path, code) if not bars.empty else None
    return _response(code, bars, minute=False,
                     source=meta["source"] if meta else "unknown",
                     updated_at=meta["fetched_at"] if meta else "", error=error)


@router.get("/minute/{code}")
def get_minute_kline(code: str) -> dict[str, Any]:
    settings = load_settings()
    archive_dir = getattr(settings.data, "archive_dir", "data/archive")
    error = ""
    try:
        bars = archive.load_minute_bars_with_archive(settings.data.db_path, archive_dir, code)
    except Exception as exc:
        bars = pd.DataFrame()
        error = f"本地分钟线缓存读取失败: {exc}"
    latest = str(bars["date"].astype(str).max()) if not bars.empty else None
    now = exchange_now()
    ordered = bars.sort_values(["date", "time"]) if not bars.empty else bars
    state = minute_cache_state(latest, str(ordered.iloc[-1]["time"]) if not bars.empty else None, now=now)
    if state["stale"]:
        try:
            fetched = fetcher.fetch_minute_bars(code)
            if not fetched.empty:
                db.init_db(settings.data.db_path)
                db.save_minute_bars(settings.data.db_path, code, fetched,
                                    source=fetched.attrs.get("source", "unknown"))
                bars = archive.load_minute_bars_with_archive(settings.data.db_path, archive_dir, code)
        except Exception as exc:
            error = f"{error}；{exc}" if error else str(exc)
    meta = db.get_latest_minute_metadata(settings.data.db_path, code) if not bars.empty else None
    return _response(code, bars, minute=True,
                     source=meta["source"] if meta else "unknown",
                     updated_at=meta["fetched_at"] if meta else "", error=error)
