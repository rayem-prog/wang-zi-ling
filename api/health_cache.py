"""Cache full audits by market inputs; heartbeat writes do not invalidate them."""
from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import datetime as dt
import json
from hashlib import sha256
from pathlib import Path
import sqlite3
import logging
from threading import Lock
import time

from config.settings import settings_version
from data.health import assess_data_health, _expected_session
from data.timeliness import exchange_now, exchange_now as normalize_exchange_time
from data.trading_calendar import calendar_verified


def market_signature(settings):
    path = Path(settings.data.db_path).resolve()
    if not path.exists():
        return (str(path), 'missing')
    with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as conn:
        conn.execute('BEGIN')
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        from data.security_master import SECURITIES
        parts = [str(path), ('security_facts', sha256(
            json.dumps(SECURITIES, sort_keys=True, ensure_ascii=False).encode()).hexdigest())]
        if 'market_input_version' in tables:
            columns={r[1] for r in conn.execute('PRAGMA table_info(market_input_version)')}
            column='version' if settings.engine.use_intraday_factors or 'daily_version' not in columns else 'daily_version'
            row = conn.execute(f'SELECT {column} FROM market_input_version WHERE id=1').fetchone()
            if row is None:
                raise RuntimeError('行情版本记录缺失')
            parts.append(('market_input_version', row[0]))
        else:
            # Read-only legacy access cannot install triggers. Hash actual rows
            # until normal application startup migrates the database.
            for table in (('bars','minute_bars') if settings.engine.use_intraday_factors else ('bars',)):
                if table not in tables:
                    parts.append((table, 'missing'))
                    continue
                digest = sha256()
                columns = tuple(r[1] for r in conn.execute(f'PRAGMA table_info({table})'))
                digest.update(repr(columns).encode())
                for row in conn.execute(f'SELECT * FROM {table} ORDER BY code,date'):
                    digest.update(repr(row).encode())
                parts.append((table, digest.hexdigest()))
        for table in ('active_bar_snapshots', 'bar_snapshots', 'active_minute_snapshots',
                      'minute_snapshots', 'data_source_batches'):
            if 'minute' in table and not settings.engine.use_intraday_factors:
                continue
            if table in tables:
                columns = 'evidence,source,captured_at' if table == 'data_source_batches' else '*'
                where=" WHERE evidence IN (SELECT DISTINCT evidence FROM bars)" if table=='data_source_batches' and not settings.engine.use_intraday_factors else ''
                parts.append((table, tuple(conn.execute(f'SELECT {columns} FROM {table}{where} ORDER BY 1,2').fetchall())))
        universe = None
        if 'collection_universe' in tables:
            from data.db import universe_key
            universe = conn.execute('SELECT codes FROM collection_universe WHERE config=?',
                                    (universe_key(settings.universe),)).fetchone()
        if universe is not None:
            parts.append(('collection_universe', universe[0]))
        elif 'spot' in tables:
            parts.append(('spot_universe', tuple(conn.execute('SELECT code FROM spot ORDER BY code').fetchall())))
        if 'bar_revisions' in tables:
            where=" WHERE kind='daily'" if not settings.engine.use_intraday_factors else ''
            parts.append(('revisions', conn.execute('SELECT MAX(id) FROM bar_revisions'+where).fetchone()[0]))
        # This detects replacement even if dates and versions happen to be identical.
        stat = path.stat()
        parts.append(('file', stat.st_dev, stat.st_ino))
    return tuple(parts)


def minute_signature(settings):
    path=Path(settings.data.db_path).resolve()
    if not path.exists():
        return None
    with closing(sqlite3.connect(f'file:{path}?mode=ro',uri=True)) as conn:
        columns={r[1] for r in conn.execute('PRAGMA table_info(market_input_version)')}
        if not columns:
            return None
        column='minute_version' if 'minute_version' in columns else 'version'
        row=conn.execute(f'SELECT {column} FROM market_input_version WHERE id=1').fetchone()
        return row[0] if row else None


class HealthCache:
    def __init__(self, audit=assess_data_health, ttl_seconds=120., clock=time.monotonic,
                 signature=market_signature):
        self.audit, self.ttl_seconds, self.clock, self.signature = audit, ttl_seconds, clock, signature
        self._lock = Lock()
        self._entries = {}
        self._minute_entries = {}
        self._refresh_lock = Lock()
        self._refreshing = set()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='health-audit')

    def _persist(self, settings, health, status, signature=None, now=None):
        from services.result_pipeline import record_health_projection
        from services.result_store import ResultStoreError
        try:
            record_health_projection(settings, health, status, signature=signature,
                                     now=now, ttl_seconds=self.ttl_seconds)
        except ResultStoreError:
            logging.getLogger('stockpilot.health').exception('健康审计状态无法持久化')

    def _cached_result(self, settings, key, old):
        result=deepcopy(old[2])
        if not settings.engine.use_intraday_factors:
            current_minute=minute_signature(settings)
            if current_minute!=self._minute_entries.get(key):
                result.update(minute_stale=True,minute_reason='分钟数据已更新，需单独重新核验')
                from data.health import health_conditions
                result['conditions']=health_conditions(result)
                self._entries[key]=(old[0],old[1],deepcopy(result))
                self._minute_entries[key]=current_minute
                self._persist(settings,result,'passed' if result.get('ready') else 'failed',signature=old[0][0])
        return result

    @staticmethod
    def _time_key(settings, current):
        if settings.engine.use_intraday_factors:
            return current.strftime('%Y-%m-%d %H:%M')
        return (_expected_session(current).isoformat(), calendar_verified(current.date()))

    def get_background(self, settings):
        """Never put a full historical audit on a browser request's critical path."""
        key = (str(Path(settings.data.db_path).resolve()), settings_version(settings))
        old = self._entries.get(key)
        try:
            signature = (self.signature(settings), self._time_key(settings, normalize_exchange_time(exchange_now())))
            if old and old[0] == signature and self.clock()-old[1] < self.ttl_seconds:
                return self._cached_result(settings,key,old)
        except Exception:
            pass  # Revalidation failure cannot make an old ready entry usable.
        pending = deepcopy(old[2]) if old else dict(trade_date=None, coverage=0.)
        pending.update(ready=False, training_ready=False, health_cache_stale=True,
                       reason='正在核验完整行情历史，请稍后刷新', health_checked_at=None)
        with self._refresh_lock:
            if key not in self._refreshing:
                self._refreshing.add(key)
                self._persist(settings, pending, 'pending')
                self._pool.submit(self._refresh, settings, key)
        return pending

    def _refresh(self, settings, key):
        try:
            self.get(settings, wait=True)
        finally:
            with self._refresh_lock:
                self._refreshing.discard(key)

    def get(self, settings, *, wait=False):
        key = (str(Path(settings.data.db_path).resolve()), settings_version(settings))
        if not self._lock.acquire(blocking=wait):
            old = self._entries.get(key)
            pending = deepcopy(old[2]) if old else dict(trade_date=None, coverage=0.)
            pending.update(ready=False, training_ready=False, health_cache_stale=True,
                           reason='正在核验完整行情历史，请稍后刷新', health_checked_at=None)
            return pending
        try:
            now = self.clock()
            current = normalize_exchange_time(exchange_now())
            finished = current
            try:
                signature = (self.signature(settings), self._time_key(settings, current))
                old = self._entries.get(key)
                if old and old[0] == signature and now - old[1] < self.ttl_seconds:
                    result = self._cached_result(settings,key,old)
                    # A queued recheck may have recorded pending before another
                    # worker completed this audit. Restore its existing certificate;
                    # checked_at still caps the expiry and cannot be renewed here.
                    self._persist(settings,result,'passed' if result.get('ready') else 'failed',
                                  signature=signature[0],now=current)
                    return result
                self._persist(settings, old[2] if old else {}, 'running', now=current)
                minute_before=minute_signature(settings)
                result = dict(self.audit(settings))
                finished = normalize_exchange_time(exchange_now())
                after = (self.signature(settings), self._time_key(settings, finished))
                if minute_before!=minute_signature(settings) and not settings.engine.use_intraday_factors:
                    result.update(minute_stale=True,minute_reason='分钟数据核验期间变化，需单独重新核验')
                    from data.health import health_conditions
                    result['conditions']=health_conditions(result)
                session_changed = (_expected_session(current) != _expected_session(finished)
                    or calendar_verified(current.date()) != calendar_verified(finished.date()))
                minute_changed = settings.engine.use_intraday_factors and after[1] != signature[1]
                if after[0] != signature[0] or session_changed or minute_changed:
                    raise RuntimeError('审计期间行情版本或有效交易时点发生变化，请重新检查')
                result['health_as_of'] = current.isoformat()
                result.update(health_cache_stale=False,
                    health_checked_at=finished.isoformat(timespec='seconds'))
                self._persist(settings, result, 'passed' if result.get('ready') else 'failed', signature=after[0], now=finished)
                self._entries[key] = (after, self.clock(), deepcopy(result))
                self._minute_entries[key]=minute_signature(settings)
                if len(self._entries) > 8:
                    removed=next(iter(self._entries))
                    del self._entries[removed]
                    self._minute_entries.pop(removed,None)
                return result
            except Exception as exc:
                # A failed revalidation can never resurrect a previously ready snapshot.
                prior = self._entries.pop(key, None)
                self._minute_entries.pop(key,None)
                failed = deepcopy(prior[2]) if prior else dict(trade_date=None, coverage=0.)
                failed.update(ready=False, reason=f'健康审计未完成: {exc}', health_cache_stale=True,
                              training_ready=False, health_checked_at=finished.isoformat(timespec='seconds'))
                self._persist(settings, failed, 'failed', now=finished)
                return failed

        finally:
            self._lock.release()


health_cache = HealthCache()
