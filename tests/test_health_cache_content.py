"""A ready health cache must not hide corruption of a published snapshot."""
import datetime as dt
from dataclasses import replace
import sqlite3

import pandas as pd

from config.settings import Settings
from data import db
from data.health import assess_data_health
from data.trading_calendar import expected_sessions


def test_ready_cache_reaudits_in_place_published_bar_change(tmp_path, monkeypatch):
    from api import health_cache as cache_module

    now = dt.datetime(2026, 9, 14, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
    monkeypatch.setattr(cache_module, "exchange_now", lambda: now)
    path = str(tmp_path / "market.db")
    db.init_db(path)
    settings = replace(Settings(), data=replace(Settings().data, db_path=path,
                                                start_date="2026-06-01"))
    frame = pd.DataFrame([
        dict(date=day, open=10, high=11, low=9, close=10, volume=100, amount=1000)
        for day in expected_sessions("2026-06-01", "2026-09-11")
    ])
    for code, adjustment in [("000001", "qfq"), ("sh000300", "none")]:
        db.publish_bar_snapshot(path, code, frame, source="test", adjustment=adjustment,
                                adjustment_version="v1", evidence="test://fixture",
                                reason="isolated regression fixture")
    calls = []

    def audit(value):
        calls.append(1)
        return assess_data_health(value, now=now)

    cache = cache_module.HealthCache(audit=audit, clock=lambda: 0.)
    assert cache.get(settings)["ready"] is True
    assert len(calls) == 1
    # Count, dates, fetched_at, registered hashes and version stay identical.
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE bars SET close=10.5 WHERE code='000001' AND date='2026-09-11'")
    assert assess_data_health(settings, now=now)["ready"] is False
    assert cache.get(settings)["ready"] is False
    assert len(calls) == 2


def test_market_input_version_tracks_content_but_not_heartbeat(tmp_path):
    path = str(tmp_path / 'version.db')
    db.init_db(path)
    with sqlite3.connect(path) as connection:
        version = lambda: connection.execute('SELECT version FROM market_input_version WHERE id=1').fetchone()[0]
        first = version()
        connection.execute("INSERT INTO bars(code,date,close) VALUES('000001','2026-09-11',10)")
        assert version() == first + 1
        connection.execute("UPDATE bars SET close=11 WHERE code='000001'")
        assert version() == first + 2
        connection.execute("DELETE FROM bars WHERE code='000001'")
        assert version() == first + 3
        connection.execute("INSERT INTO minute_bars(code,date,time,close) VALUES('000001','2026-09-11','10:00',10)")
        assert version() == first + 4
        connection.commit()
        db.record_heartbeat(path,'running',config_version='fixture')
        assert version() == first + 4


def test_unmigrated_cache_signature_detects_content_change(tmp_path):
    from api.health_cache import market_signature
    path = str(tmp_path / 'legacy.db')
    settings = replace(Settings(),data=replace(Settings().data,db_path=path))
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE bars(code TEXT,date TEXT,close REAL)')
        connection.execute("INSERT INTO bars VALUES('000001','2026-09-11',10)")
    before = market_signature(settings)
    with sqlite3.connect(path) as connection:
        connection.execute('UPDATE bars SET close=11')
    assert market_signature(settings) != before


def test_parallel_health_request_never_waits_for_full_history_audit(tmp_path):
    import threading,time
    from api.health_cache import HealthCache
    settings=Settings()
    entered=threading.Event();release=threading.Event()
    def audit(_):
        entered.set();release.wait(3)
        return {'ready':True,'trade_date':'2026-10-09'}
    cache=HealthCache(audit=audit,signature=lambda _: 'fixed')
    worker=threading.Thread(target=cache.get,args=(settings,));worker.start()
    try:
        assert entered.wait(1)
        start=time.monotonic();pending=cache.get(settings)
        assert time.monotonic()-start<.25
        assert pending['ready'] is False
        assert pending['health_cache_stale'] is True
    finally:
        release.set();worker.join(4)
