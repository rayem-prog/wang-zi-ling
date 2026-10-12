"""Holdings valuations: live quotes during trading, verified raw closes off hours."""
import datetime as dt
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
from data import db, fetcher
from data.baseline import capture_batch
from data.health import _expected_session
from data.timeliness import exchange_now, quote_state
from data.trading_calendar import calendar_verified, closure_kind

_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='holding-closes')
_lock = threading.Lock()
_running = set()
_errors = {}


def cache_path(settings, date):
    # Keep raw valuation prices separate from the adjusted training snapshots.
    return Path(settings.paths.artifact_dir) / 'holding-closes' / f'{date}.db'


def refresh_closes(settings, codes, date):
    path = str(cache_path(settings, date))
    db.init_db(path)
    for code in codes:
        key = (path, code)
        try:
            frame = fetcher.fetch_daily(code, start=date, end=date, adjust='')
            if frame.empty or set(frame.date.astype(str)) != {date} or set(frame.code.astype(str)) != {code}:
                raise ValueError(f'数据源未返回 {date} 的该股票收盘日线（可能停牌或采集缺失）')
            if frame.attrs.get('adjustment') != 'none':
                raise ValueError('收盘估值需要未复权价格')
            conn = db._connect(path)
            try:
                with conn:
                    capture_batch(conn, frame)
            finally:
                conn.close()
            db.publish_bar_snapshot(path, code, frame, source=frame.attrs.get('source', ''),
                adjustment='none', adjustment_version=frame.attrs.get('adjustment_version', ''),
                evidence=frame.attrs.get('evidence', ''), reason='实际持仓收盘估值采集')
            with _lock:
                _errors.pop(key, None)
        except Exception as exc:
            with _lock:
                _errors[key] = (time.monotonic(), str(exc))
        finally:
            with _lock:
                _running.discard(key)


def valuation_quotes(settings, positions, spot, *, now=None, refresh=True):
    current = exchange_now(now)
    clock = current.time().replace(tzinfo=None)
    closed = bool(closure_kind(current.date())) or clock < dt.time(9, 15) or clock >= dt.time(16, 10)
    date = _expected_session(current).isoformat()
    path = str(cache_path(settings, date))
    states = {str(r['code']): dict(quote_state(r, now=current), price_basis='quote')
              for r in spot.to_dict('records')}
    prices = {str(r['code']): r['price'] for r in spot.to_dict('records')}
    rows, metadata, missing = [], {}, []
    for code in positions.code.astype(str):
        state = states.get(code, dict(stale=True, reason='没有该持仓的可信报价',
                     trade_date=None, quote_time=None, price_source=None, price_basis='unavailable')).copy()
        price = prices.get(code)
        if closed and calendar_verified(current.date()):
            frame = (db.load_training_bars(path, code, start=date, end=date, adjustment='none')
                     if Path(path).exists() else pd.DataFrame())
            if not frame.empty:
                row = frame.iloc[-1]
                price = float(row.close)
                state = dict(stale=False, reason='', trade_date=date, quote_time=f'{date}T15:00:00+08:00',
                             price_source=row.source, price_basis='close')
            elif state['stale']:
                missing.append(code)
                with _lock:
                    error = _errors.get((path, code))
                state['reason'] = error[1] if error else f'正在核验 {date} 未复权收盘价'
        if not state['stale'] and price is not None and pd.notna(price) and float(price) > 0:
            rows.append(dict(code=code, price=float(price)))
        else:
            state.update(stale=True, price_basis='unavailable')
        metadata[code] = state
    if refresh and missing:
        with _lock:
            todo = [c for c in missing if (path, c) not in _running and
                    time.monotonic() - _errors.get((path, c), (-100, ''))[0] >= 60]
            _running.update((path, c) for c in todo)
        if todo:
            _worker.submit(refresh_closes, settings, todo, date)
    with _lock:
        pending = any((path, c) in _running for c in missing)
    return pd.DataFrame(rows, columns=['code', 'price']), metadata, pending
