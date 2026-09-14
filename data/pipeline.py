"""数据更新管道：增量拉取、分钟线采集、落库、标记新鲜度。"""

from __future__ import annotations

import datetime as dt
import random
import time

import pandas as pd

from data import db, fetcher


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



def update_daily_incremental(settings, codes: list[str] | None = None, sleep_jitter: bool = True) -> dict:
    if codes is None:
        codes = fetch_universe(settings)
    db.init_db(settings.data.db_path)
    updated: list[str] = []
    failed: list[str] = []
    skipped: list[str] = []
    bars_added = 0

    end_date_str = settings.data.end_date or dt.date.today().strftime("%Y-%m-%d")

    for code in codes:
        try:
            latest = db.get_latest_bar_date(settings.data.db_path, code)
            if latest:
                next_day = (pd.to_datetime(latest) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                if next_day > end_date_str:
                    skipped.append(code)
                    continue
                start_date = next_day
            else:
                start_date = settings.data.start_date

            if sleep_jitter:
                time.sleep(random.uniform(0.3, 0.8))

            bars = _fetch_with_backoff(
                fetcher.fetch_daily,
                code,
                start=start_date,
                end=end_date_str,
                adjust=settings.data.adjust,
            )
            if bars.empty:
                skipped.append(code)
                continue

            bars_added += db.save_bars(settings.data.db_path, code, bars)
            db.mark_fresh(settings.data.db_path, code)
            updated.append(code)
        except Exception:
            failed.append(code)

    # Benchmark index incremental update
    bench_updated = False
    try:
        idx = settings.data.index_symbol
        latest_bench = db.get_latest_bar_date(settings.data.db_path, idx)
        if latest_bench:
            b_start = (pd.to_datetime(latest_bench) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            b_start = settings.data.start_date

        if b_start <= end_date_str:
            bench = _fetch_with_backoff(
                fetcher.fetch_index_daily,
                idx,
                start=b_start,
                end=end_date_str,
            )
            if not bench.empty:
                db.save_bars(settings.data.db_path, idx, bench)
                db.mark_fresh(settings.data.db_path, idx)
                bench_updated = True
        else:
            bench_updated = True
    except Exception:
        bench_updated = False

    return {
        "updated": updated,
        "failed": failed,
        "skipped": skipped,
        "bars_added": bars_added,
        "fresh_codes": updated + skipped,
        "bench_updated": bench_updated,
    }


def update_all(settings) -> dict:
    return update_daily_incremental(settings, sleep_jitter=False)


def update_minute_bars(
    settings,
    codes: list[str] | None = None,
    date: str | None = None,
    sleep_jitter: bool = False,
) -> dict:
    if codes is None:
        codes = fetch_universe(settings)
    db.init_db(settings.data.db_path)
    updated: list[str] = []
    failed: list[str] = []
    bars_added = 0

    for code in codes:
        try:
            if sleep_jitter:
                time.sleep(random.uniform(0.3, 0.8))
            mbars = _fetch_with_backoff(fetcher.fetch_minute_bars, code, date=date)
            if mbars.empty:
                failed.append(code)
                continue
            added = db.save_minute_bars(settings.data.db_path, code, mbars)
            bars_added += added
            updated.append(code)
        except Exception:
            failed.append(code)

    return {
        "updated": updated,
        "failed": failed,
        "bars_added": bars_added,
    }


def spot_snapshot(settings, codes: list[str]) -> object:
    quotes = fetcher.fetch_spot_batch(codes)
    if not quotes.empty:
        db.save_spot(settings.data.db_path, quotes)
    return quotes


def fetch_universe(settings) -> list[str]:
    return fetcher.fetch_universe(
        base_pool=settings.universe.base_pool,
        extra_codes=tuple(settings.universe.extra_codes),
    )

