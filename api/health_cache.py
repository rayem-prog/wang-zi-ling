"""Cache full audits by market inputs; heartbeat writes do not invalidate them."""
from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
from hashlib import sha256
from pathlib import Path
import sqlite3
from threading import Lock
import time

from config.settings import settings_version
from data.health import assess_data_health, _expected_session
from data.timeliness import exchange_now
from data.trading_calendar import calendar_verified


def market_signature(settings):
    path = Path(settings.data.db_path).resolve()
    if not path.exists():
        return (str(path), 'missing')
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as conn:
        conn.execute('BEGIN')
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        parts = [str(path)]
        if 'market_input_version' in tables:
            row = conn.execute('SELECT version FROM market_input_version WHERE id=1').fetchone()
            if row is None:
                raise RuntimeError('行情版本记录缺失')
            parts.append(('market_input_version', row[0]))
        else:
            # Read-only legacy access cannot install triggers. Hash actual rows
            # until normal application startup migrates the database.
            for table in ('bars', 'minute_bars'):
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
            if table in tables:
                columns = 'evidence,source,captured_at' if table == 'data_source_batches' else '*'
                parts.append((table, tuple(conn.execute(f'SELECT {columns} FROM {table} ORDER BY 1,2').fetchall())))
        if 'spot' in tables:
            parts.append(('spot_universe', tuple(conn.execute('SELECT code FROM spot ORDER BY code').fetchall())))
        if 'bar_revisions' in tables:
            parts.append(('revisions', conn.execute('SELECT MAX(id) FROM bar_revisions').fetchone()[0]))
        # This detects replacement even if dates and versions happen to be identical.
        stat = path.stat()
        parts.append(('file', stat.st_dev, stat.st_ino))
    return tuple(parts)


class HealthCache:
    def __init__(self, audit=assess_data_health, ttl_seconds=120., clock=time.monotonic,
                 signature=market_signature):
        self.audit, self.ttl_seconds, self.clock, self.signature = audit, ttl_seconds, clock, signature
        self._lock = Lock()
        self._entries = {}
        self._refresh_lock = Lock()
        self._refreshing = set()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='health-audit')

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
            signature = (self.signature(settings), self._time_key(settings, exchange_now()))
            if old and old[0] == signature and self.clock()-old[1] < self.ttl_seconds:
                return deepcopy(old[2])
        except Exception:
            pass  # Revalidation failure cannot make an old ready entry usable.
        pending = deepcopy(old[2]) if old else dict(trade_date=None, coverage=0.)
        pending.update(ready=False, training_ready=False, health_cache_stale=True,
                       reason='正在核验完整行情历史，请稍后刷新', health_checked_at=None)
        with self._refresh_lock:
            if key not in self._refreshing:
                self._refreshing.add(key)
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
            current = exchange_now()
            try:
                signature = (self.signature(settings), self._time_key(settings, current))
                old = self._entries.get(key)
                if old and old[0] == signature and now - old[1] < self.ttl_seconds:
                    return deepcopy(old[2])
                result = dict(self.audit(settings))
                finished = exchange_now()
                after = (self.signature(settings), self._time_key(settings, finished))
                session_changed = (_expected_session(current) != _expected_session(finished)
                    or calendar_verified(current.date()) != calendar_verified(finished.date()))
                minute_changed = settings.engine.use_intraday_factors and after[1] != signature[1]
                if after[0] != signature[0] or session_changed or minute_changed:
                    raise RuntimeError('审计期间行情版本或有效交易时点发生变化，请重新检查')
                result['health_as_of'] = current.isoformat()
                result.update(health_cache_stale=False,
                    health_checked_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'))
                self._entries[key] = (after, self.clock(), deepcopy(result))
                if len(self._entries) > 8:
                    del self._entries[next(iter(self._entries))]
                return result
            except Exception as exc:
                # A failed revalidation can never resurrect a previously ready snapshot.
                self._entries.pop(key, None)
                return dict(ready=False, reason=f'健康审计未完成: {exc}', health_cache_stale=True,
                            health_checked_at=None, trade_date=None, coverage=0.)

        finally:
            self._lock.release()


health_cache = HealthCache()
