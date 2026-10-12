"""Reading a ready leaderboard shares audits and never blocks on a new audit."""
import threading
import time

from api.health_cache import HealthCache
from config.settings import Settings


def test_background_read_returns_promptly_then_ready_and_invalidates_changes():
    entered = threading.Event()
    release = threading.Event()
    version = ['v1']
    def audit(_):
        entered.set()
        release.wait(3)
        return {'ready': True, 'trade_date': '2026-10-09'}
    cache = HealthCache(audit=audit, signature=lambda _: version[0])
    started = time.monotonic()
    first = cache.get_background(Settings())
    assert time.monotonic()-started < .25
    assert first['ready'] is False
    assert entered.wait(1)
    release.set()
    deadline = time.monotonic()+2
    while time.monotonic() < deadline:
        ready = cache.get_background(Settings())
        if ready['ready']:
            break
        time.sleep(.01)
    assert ready['ready'] is True
    version[0] = 'v2'
    assert cache.get_background(Settings())['ready'] is False


def test_daily_cached_audit_survives_clock_minute_changes(tmp_path, monkeypatch):
    import datetime as dt
    import api.health_cache as module
    clock = [dt.datetime(2026,10,11,21,0,tzinfo=dt.timezone(dt.timedelta(hours=8)))]
    monkeypatch.setattr(module, 'exchange_now', lambda: clock[0])
    calls = []
    cache = HealthCache(audit=lambda _: (calls.append(1) or {'ready':True}),
                        signature=lambda _: 'fixed', clock=lambda: 0.)
    assert cache.get(Settings())['ready']
    clock[0] += dt.timedelta(minutes=1)
    assert cache.get(Settings())['ready']
    assert len(calls) == 1
