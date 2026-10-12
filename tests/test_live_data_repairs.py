"""Live readiness cache and per-symbol quote-time requirements."""
import datetime as dt
from dataclasses import replace
import pandas as pd

from config.settings import Settings
from data import db
from strategy import holdings


def test_health_cache_reuses_only_same_version_and_reaudits_on_expiry():
    from api.health_cache import HealthCache
    calls = []; clock = [0.]; version = ['v1']
    def audit(settings):
        calls.append(1); return {'ready': True, 'reason': ''}
    cache = HealthCache(audit=audit, clock=lambda: clock[0], signature=lambda _: version[0], ttl_seconds=10)
    assert cache.get(Settings())['ready'] is True
    assert cache.get(Settings())['ready'] is True
    assert len(calls) == 1
    version[0] = 'v2'
    assert cache.get(Settings())['ready'] is True
    assert len(calls) == 2
    clock[0] = 11
    assert cache.get(Settings())['ready'] is True
    assert len(calls) == 3


def test_cache_does_not_reuse_ready_after_failed_reaudit():
    from api.health_cache import HealthCache
    calls = []; clock = [0.]
    def audit(settings):
        calls.append(1)
        if len(calls) > 1: raise OSError('changed database unreadable')
        return {'ready': True, 'reason': ''}
    cache = HealthCache(audit=audit, clock=lambda: clock[0], signature=lambda _: 'v1', ttl_seconds=1)
    assert cache.get(Settings())['ready'] is True
    clock[0] = 2
    result = cache.get(Settings())
    assert result['ready'] is False
    assert result['health_cache_stale'] is True
    assert 'unreadable' in result['reason']


def test_health_cache_ignores_heartbeat_but_invalidates_published_data():
    from api.health_cache import HealthCache
    s = Settings(); calls = []
    cache = HealthCache(audit=lambda _: (calls.append(1) or {'ready': False, 'reason': 'no trusted data'}))
    cache.get(s)
    db.record_heartbeat(s.data.db_path, 'running', pid=123)
    cache.get(s)
    assert len(calls) == 1
    bars = pd.DataFrame([dict(code='000001', date='2026-09-14',open=10,high=10,low=10,close=10,volume=100,amount=1000)])
    db.publish_bar_snapshot(s.data.db_path,'000001',bars,source='test',adjustment='qfq',
        adjustment_version='v1',evidence='test://quote',reason='fixture')
    cache.get(s)
    assert len(calls) == 2


def test_fresh_other_symbol_does_not_mask_stale_holding(monkeypatch):
    from api.routers import holdings as route
    from data import timeliness
    now = dt.datetime(2026,9,14,10,0)
    monkeypatch.setattr(timeliness, 'exchange_now', lambda value=None: now.replace(tzinfo=timeliness.SHANGHAI))
    holdings.save_positions('data/market.db', pd.DataFrame([
        dict(code='000001',name='fresh',shares=100,cost=10),
        dict(code='000002',name='stale',shares=100,cost=10),
    ]))
    quotes = pd.DataFrame([
        dict(code='000001',name='fresh',price=11,pct_change=1,volume=100,amount=1100,trade_date='2026-09-14',quote_time='20260914095900'),
        dict(code='000002',name='stale',price=30,pct_change=1,volume=100,amount=3000,trade_date='2026-09-14',quote_time='20260914093000'),
    ])
    db.save_spot('data/market.db',quotes)
    result = route.get_holdings_and_diagnostics()
    rows = {r['code']:r for r in result['diagnostics']}
    assert rows['000001']['price'] == 11
    assert rows['000001']['price_stale'] is False
    assert rows['000002']['price'] is None
    assert rows['000002']['price_stale'] is True
    assert rows['000002']['action'] == '数据不足'


def test_quote_state_requires_quote_time_not_capture_time():
    from data.timeliness import quote_state
    now = dt.datetime(2026,9,14,10,0)
    assert quote_state({'trade_date':'2026-09-14','ts':now.isoformat()},now=now)['stale'] is True
    assert quote_state({'trade_date':'2026-09-14','quote_time':'20260914095900'},now=now)['stale'] is False
    assert quote_state({'trade_date':'2026-09-14','quote_time':'20260914103000'},now=now)['stale'] is True


def test_intraday_stale_quote_does_not_trigger_exit(monkeypatch):
    from api.routers import intraday
    from data import timeliness
    now = dt.datetime(2026,9,14,10,0)
    monkeypatch.setattr(timeliness,'exchange_now',lambda value=None: now.replace(tzinfo=timeliness.SHANGHAI))
    holdings.save_positions('data/market.db',pd.DataFrame([dict(code='000001',name='stale',shares=100,cost=10)]))
    monkeypatch.setattr(intraday.fetcher,'fetch_spot_batch',lambda codes:pd.DataFrame([
        dict(code='000001',name='stale',price=8,pct_change=-5,trade_date='2026-09-14',quote_time='20260914093000'),
    ]))
    result = intraday.get_intraday_decisions()
    assert result['holdings_decisions'][0]['status'] == 'DATA_UNAVAILABLE'
    assert result['holdings_decisions'][0]['price_stale'] is True
    assert result['summary']['urgent_count'] == 0


def test_health_changed_during_audit_cannot_be_cached_ready():
    from api.health_cache import HealthCache
    version = ['v1']
    def audit(settings):
        version[0] = 'v2'
        return {'ready': True, 'reason': ''}
    cache = HealthCache(audit=audit,signature=lambda _:version[0])
    assert cache.get(Settings())['ready'] is False


def test_health_cache_reaudits_when_spot_universe_changes():
    from api.health_cache import HealthCache
    calls = []
    cache = HealthCache(audit=lambda _: (calls.append(1) or {'ready':False,'reason':'fixture'}))
    settings = Settings()
    cache.get(settings)
    db.save_spot(settings.data.db_path,pd.DataFrame([dict(code='000001',name='new',price=10,
        pct_change=0,volume=100,amount=1000,trade_date='2026-09-14',quote_time='20260914100000')]))
    cache.get(settings)
    assert len(calls) == 2
