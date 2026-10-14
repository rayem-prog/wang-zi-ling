"""数据更新管道：增量拉取、分钟线采集、落库、标记新鲜度。"""

from __future__ import annotations

import datetime as dt
import random
import time

import pandas as pd

from data import db, fetcher
from data.health import _expected_session
from data.security_master import inactive_status
from data.trading_calendar import closure_kind


def _fetch_with_backoff(fetch_fn, *args, max_retries: int = 3, backoff_sleep: bool = True, **kwargs):
    last_err = None
    for attempt in range(max_retries):
        try:
            return fetch_fn(*args, **kwargs)
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1 and backoff_sleep:
                time.sleep(2**attempt)
    raise last_err or RuntimeError("fetch failed after retries")


def _filter_inactive_codes(codes: list[str], trade_date: str) -> tuple[list[str], list[dict]]:
    active, inactive = [], []
    for code in codes:
        status = inactive_status(code, trade_date)
        if status:
            inactive.append(status)
        else:
            active.append(code)
    return active, inactive



def update_daily_incremental(settings, codes: list[str] | None = None,
                             sleep_jitter: bool = True, mode: str = "both") -> dict:
    if mode not in {"both", "current", "history"}:
        raise ValueError(f"未知补采模式: {mode}")
    if codes is None:
        codes = fetch_universe(settings)
    db.init_db(settings.data.db_path)
    updated: list[str] = []
    failed: list[str] = []
    skipped: list[str] = []
    history_short: list[str] = []
    failed_details: dict[str, str] = {}
    deferred: list[str] = []
    consecutive_errors = 0
    bars_added = 0

    now = dt.datetime.now()
    end_date_str = settings.data.end_date or _expected_session(now).isoformat()
    history_cutoff = (dt.date.fromisoformat(settings.data.start_date) + dt.timedelta(days=30)).isoformat()
    excluded_inactive = []
    if mode != "history":
        codes, excluded_inactive = _filter_inactive_codes(codes, end_date_str)

    for index, code in enumerate(codes):
        if consecutive_errors >= 5:
            deferred.extend(codes[index:])
            break
        added_for_code = 0
        successful_fetch = False
        try:
            first = db.get_first_bar_date(settings.data.db_path, code)
            latest = db.get_latest_bar_date(settings.data.db_path, code)
        except Exception as exc:
            failed.append(code)
            failed_details[code] = f"读取本地日线状态失败: {exc}"
            continue
        intervals = []
        # 先补最新区间。历史源限流时仍可尽量修复当前交易日覆盖。
        if latest:
            next_day = (pd.to_datetime(latest) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            if mode != "history" and next_day <= end_date_str:
                intervals.append((next_day, end_date_str, "current"))
            if mode != "current" and first and first > history_cutoff:
                history_end = (pd.to_datetime(first) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                intervals.append((settings.data.start_date, history_end, "history"))
        else:
            if mode != "current":
                intervals.append((settings.data.start_date, end_date_str, "history"))
            else:
                current_start = max(settings.data.start_date,
                                    (dt.date.fromisoformat(end_date_str) - dt.timedelta(days=90)).isoformat())
                intervals.append((current_start, end_date_str, "current"))

        for start_date, interval_end, kind in intervals:
            if sleep_jitter:
                time.sleep(random.uniform(0.3, 0.8))
            try:
                bars = _fetch_with_backoff(
                    fetcher.fetch_daily, code, start=start_date, end=interval_end,
                    adjust=settings.data.adjust,
                )
                successful_fetch = True
                source = bars.attrs.get("source", "unknown")
                added = db.save_bars(settings.data.db_path, code, bars, source=source) if not bars.empty else 0
                db.record_fetch(settings.data.db_path, code, start_date, interval_end,
                                source, "ok" if not bars.empty else "empty", added)
                added_for_code += added
                if bars.empty and kind == "history":
                    history_short.append(code)
            except Exception as exc:
                db.record_fetch(settings.data.db_path, code, start_date, interval_end,
                                "unknown", "error", 0, str(exc))
                if code not in failed:
                    failed.append(code)
                failed_details[code] = (failed_details.get(code, "") +
                                        f"{kind} {start_date}~{interval_end}: {exc}; ")
        bars_added += added_for_code
        if added_for_code:
            db.mark_fresh(settings.data.db_path, code)
            updated.append(code)
        else:
            skipped.append(code)
        if intervals:
            consecutive_errors = 0 if successful_fetch else consecutive_errors + 1

    # 指数当前区间同样先更新；历史区间失败不得阻断当前行情。
    bench_updated = False
    idx = settings.data.index_symbol
    try:
        first_bench = db.get_first_bar_date(settings.data.db_path, idx)
        latest_bench = db.get_latest_bar_date(settings.data.db_path, idx)
        bench_intervals = []
        if latest_bench:
            b_start = (pd.to_datetime(latest_bench) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            if mode != "history" and b_start <= end_date_str:
                bench_intervals.append((b_start, end_date_str, "current"))
            if mode != "current" and first_bench and first_bench > history_cutoff:
                b_end = (pd.to_datetime(first_bench) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                bench_intervals.append((settings.data.start_date, b_end, "history"))
        else:
            bench_start = (max(settings.data.start_date,
                               (dt.date.fromisoformat(end_date_str) - dt.timedelta(days=90)).isoformat())
                           if mode == "current" else settings.data.start_date)
            bench_intervals.append((bench_start, end_date_str,
                                    "current" if mode == "current" else "history"))
        for b_start, b_end, kind in bench_intervals:
            try:
                bench = _fetch_with_backoff(fetcher.fetch_index_daily, idx,
                                            start=b_start, end=b_end)
                source = bench.attrs.get("source", "unknown")
                added = db.save_bars(settings.data.db_path, idx, bench, source=source) if not bench.empty else 0
                db.record_fetch(settings.data.db_path, idx, b_start, b_end,
                                source, "ok" if not bench.empty else "empty", added)
                if added:
                    db.mark_fresh(settings.data.db_path, idx)
                    bench_updated = True
            except Exception as exc:
                db.record_fetch(settings.data.db_path, idx, b_start, b_end,
                                "unknown", "error", 0, str(exc))
                failed_details[idx] = (failed_details.get(idx, "") +
                                       f"{kind} {b_start}~{b_end}: {exc}; ")
    except Exception as exc:
        failed_details[idx] = f"读取本地指数状态失败: {exc}"

    latest_bench_date = db.get_latest_bar_date(settings.data.db_path, settings.data.index_symbol)
    unverified_missing = [
        code for code in codes if code not in failed and latest_bench_date
        and (db.get_latest_bar_date(settings.data.db_path, code) or "") < latest_bench_date
    ]
    non_trading_day = closure_kind(dt.date.fromisoformat(settings.data.end_date or now.date().isoformat())) is not None

    return {
        "requested_codes": list(codes),
        "excluded_inactive_codes": excluded_inactive,
        "updated": updated,
        "failed": failed,
        "failed_details": failed_details,
        "deferred": deferred,
        "source_unavailable": bool(deferred),
        "skipped": skipped,
        "history_short": history_short,
        "unverified_suspension_or_gap": unverified_missing,
        "non_trading_day": non_trading_day,
        "target_trade_date": end_date_str,
        "bars_added": bars_added,
        "fresh_codes": [code for code in codes
                        if (db.get_latest_bar_date(settings.data.db_path, code) or "") >= end_date_str],
        "bench_updated": bench_updated,
    }


def update_all(settings, progress=None) -> dict:
    def stage(name):
        if progress:
            progress(name)
    from data.verified_daily import update_verified_daily
    stage('fetch_daily')
    result = update_verified_daily(settings)
    from dataclasses import replace
    from pathlib import Path
    raw_path = Path(settings.data.db_path).with_name('raw-' + Path(settings.data.db_path).name)
    raw_settings = replace(settings, data=replace(settings.data, db_path=str(raw_path), adjust='none'))
    stage('fetch_raw_daily')
    result['raw_update'] = update_verified_daily(raw_settings, codes=result['requested_codes'])
    # 日线、报价与分钟缓存分别记录结果，辅助数据失败不伪装成整体成功。
    try:
        stage('fetch_spot')
        quotes = spot_snapshot(settings, result['requested_codes'])
        result['spot_rows'] = len(quotes)
    except Exception as exc:
        result['spot_error'] = str(exc)
    from data.incremental import minute_missing_times
    from data.timeliness import collection_minute_target
    minute_date,through=collection_minute_target()
    if minute_date is None:
        minute_date,through=result['target_trade_date'],'15:00'
    minute_gaps={}
    for code in result['requested_codes']:
        frame=db.load_training_minute_bars(settings.data.db_path,code,date=minute_date)
        gaps=minute_missing_times(frame,through=through)
        if gaps:
            minute_gaps[code]=gaps
    missing=list(minute_gaps)
    result['minute_gaps']=minute_gaps
    result['minute_target_date']=minute_date
    result['minute_required_through']=through
    result['minute_update']=dict(requested_codes=missing,updated=[],failed=[],failed_details={},empty=[],bars_added=0)
    if missing:
        stage('fetch_minute')
        result['minute_update'] = update_minute_bars(settings, codes=missing,
                                                   date=minute_date, require_complete=True, required_through=through)
    return result


def update_minute_bars(
    settings,
    codes: list[str] | None = None,
    date: str | None = None,
    sleep_jitter: bool = False,
    require_complete: bool = False,
    required_through: str = '15:00',
) -> dict:
    if codes is None:
        codes = fetch_universe(settings)
    db.init_db(settings.data.db_path)
    target_date = date or _expected_session(dt.datetime.now()).isoformat()
    codes, excluded_inactive = _filter_inactive_codes(codes, target_date)
    updated: list[str] = []
    failed: list[str] = []
    failed_details: dict[str, str] = {}
    empty: list[str] = []
    bars_added = 0

    for code in codes:
        try:
            if sleep_jitter:
                time.sleep(random.uniform(0.3, 0.8))
            mbars = _fetch_with_backoff(fetcher.fetch_minute_bars, code, date=date)
            if mbars.empty:
                empty.append(code)
                db.record_fetch(settings.data.db_path, code, date or "", date or "",
                                "unknown", "empty", 0, kind="minute")
                continue
            source = mbars.attrs.get("source", "unknown")
            if require_complete:
                from data.incremental import minute_missing_times
                gaps=minute_missing_times(mbars,through=required_through)
                if gaps:
                    raise ValueError('分钟响应不完整，缺少: '+','.join(gaps))
            added = db.save_minute_bars(settings.data.db_path, code, mbars, source=source)
            db.record_fetch(settings.data.db_path, code, date or "", date or "",
                            source, "ok", added, kind="minute")
            bars_added += added
            updated.append(code)
        except Exception as exc:
            failed.append(code)
            failed_details[code] = str(exc)
            db.record_fetch(settings.data.db_path, code, date or "", date or "",
                            "unknown", "error", 0, str(exc), kind="minute")

    return {
        "updated": updated,
        "requested_codes": codes,
        "failed": failed,
        "failed_details": failed_details,
        "empty": empty,
        "bars_added": bars_added,
        "excluded_inactive_codes": excluded_inactive,
    }


def spot_snapshot(settings, codes: list[str]) -> object:
    quotes = fetcher.fetch_spot_batch(codes)
    if not quotes.empty:
        db.save_spot(settings.data.db_path, quotes)
    return quotes


def fetch_universe(settings) -> list[str]:
    codes = fetcher.fetch_universe(
        base_pool=settings.universe.base_pool,
        extra_codes=tuple(settings.universe.extra_codes),
    )
    db.save_collection_universe(settings.data.db_path, settings.universe, codes)
    return codes
