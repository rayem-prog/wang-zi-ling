import datetime as dt
import json
import sqlite3
from dataclasses import replace

import pytest

from config.settings import Settings
from data import db
from services.result_pipeline import record_health_projection
from services.result_store import ResultStore


def test_rechecking_keeps_failure_and_missing_securities(tmp_path):
    settings = replace(Settings(), paths=replace(Settings().paths, artifact_dir=str(tmp_path)))
    now = dt.datetime.fromisoformat('2026-10-13T08:00:00+08:00')
    health = dict(ready=False, reason='股票可信覆盖不足', missing_codes=['000568'],
                  expected_trade_date='2026-10-12')
    record_health_projection(settings, health, 'failed', now=now)
    record_health_projection(settings, {}, 'running', now=now + dt.timedelta(seconds=1))
    audit = ResultStore(tmp_path).read_view()['audit']
    assert audit['ready'] is False
    assert audit['error']['message'] == '股票可信覆盖不足'
    assert audit['health']['missing_codes'] == ['000568']
    assert audit['started_at'] == (now + dt.timedelta(seconds=1)).isoformat()


def test_market_reader_does_not_block_snapshot_writer(tmp_path):
    path = str(tmp_path / 'market.db')
    db.init_db(path)
    reader = sqlite3.connect(path)
    writer = sqlite3.connect(path, timeout=0.05)
    try:
        reader.execute('BEGIN')
        reader.execute('SELECT * FROM market_input_version').fetchall()
        writer.execute("INSERT INTO bars(code,date) VALUES('000001','2026-10-12')")
        writer.commit()
    finally:
        reader.close()
        writer.close()


@pytest.mark.parametrize('malformed', ['price', 'missing_fields'])
def test_invalid_capture_is_preserved_but_refetched(monkeypatch, tmp_path, malformed):
    from data import verified_daily as v
    calls = []
    class Response:
        def __init__(self, price):
            row = ['2026-10-12', price, price, price, price, '1', {}, '0', '1']
            self.payload = {'code': 0, 'data': {'sz000001': {'qfqday': [row]}}}
            self.content = json.dumps(self.payload).encode()
        def raise_for_status(self): pass
        def json(self): return self.payload
    class Session:
        def __init__(self): self.headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, *args, **kwargs):
            calls.append(1)
            response = Response('-1' if len(calls) == 1 else '10')
            if len(calls) == 1 and malformed == 'missing_fields':
                response.payload['data']['sz000001'] = {}
                response.content = json.dumps(response.payload).encode()
            return response
    monkeypatch.setattr(v.requests, 'Session', Session)
    monkeypatch.setattr(v.time, 'sleep', lambda *_: None)
    with pytest.raises(ValueError, match='invalid_ohlcv|缺少请求的复权模式'):
        v.capture('000001', '2026-10-12', '2026-10-12', 'qfq', tmp_path)
    frame = v.capture('000001', '2026-10-12', '2026-10-12', 'qfq', tmp_path)
    assert len(calls) == 2
    assert frame.iloc[0]['close'] == 10
    assert list(tmp_path.rglob('*invalid*.response'))


def test_snapshot_exposes_recheck_progress_and_failure():
    from services.result_contract import build_snapshot_response
    now = dt.datetime.fromisoformat('2026-10-13T08:01:00+08:00')
    audit = dict(state='running', ready=False, started_at='2026-10-13T08:00:00+08:00',
                 error=dict(code='audit_failed', message='缺失日线'),
                 health=dict(missing_codes=['000568'], expected_trade_date='2026-10-12'))
    response = build_snapshot_response(dict(snapshot=None, revocation=None, audit=audit,
                                           job=None, response_revision=1), Settings(), now=now)
    assert response['audit']['elapsed_seconds'] == 60
    assert response['audit']['missing_codes'] == ['000568']
    assert response['audit']['expected_trade_date'] == '2026-10-12'
    assert response['audit']['error']['message'] == '缺失日线'


def test_parallel_fetches_publish_on_one_writer(monkeypatch, tmp_path):
    import threading
    import pandas as pd
    from data import verified_daily as v
    settings = replace(Settings(), data=replace(Settings().data, db_path=str(tmp_path/'market.db')),
                       paths=replace(Settings().paths, artifact_dir=str(tmp_path)))
    publishing_threads = []
    def capture(code, *args):
        frame = pd.DataFrame([dict(date='2026-10-12')])
        frame.attrs = dict(source='test', adjustment='qfq', adjustment_version='test', evidence='test')
        return frame
    def publish(*args, **kwargs):
        publishing_threads.append(threading.get_ident())
        return 1
    monkeypatch.setattr(v, 'capture', capture)
    monkeypatch.setattr(db, 'publish_bar_snapshot', publish)
    result = v.update_verified_daily(settings, codes=['000001', '000002'])
    assert not result['failed']
    assert len(publishing_threads) == 3
    assert set(publishing_threads) == {threading.get_ident()}


def test_snapshot_transaction_writes_durable_owner_and_phase_log(tmp_path):
    import os
    import pandas as pd
    path = str(tmp_path/'market.db')
    db.init_db(path)
    frame = pd.DataFrame([dict(date='2026-10-12', open=10, high=11, low=9,
                               close=10, volume=100, amount=1000)])
    db.publish_bar_snapshot(path, '000001', frame, source='test', adjustment='qfq',
                            adjustment_version='v1', evidence='test://response', reason='fixture')
    log = (tmp_path/f'market.db.transactions.{os.getpid()}.log').read_text()
    assert str(os.getpid()) in log
    assert '000001' in log and 'MainThread' in log
    assert 'begin' in log and 'committed' in log and 'elapsed=' in log


def test_queued_background_audit_cache_hit_cannot_overwrite_passed_state(tmp_path, monkeypatch):
    from api.health_cache import HealthCache
    from config.settings import Settings, settings_version
    from pathlib import Path
    from dataclasses import replace
    settings=replace(Settings(),data=replace(Settings().data,db_path=str(tmp_path/'market.db')))
    cache=HealthCache(audit=lambda s: {'ready':True,'trade_date':'2026-10-12'},signature=lambda s:'stable')
    transitions=[]
    monkeypatch.setattr(cache,'_persist',lambda s,h,state,**kw: transitions.append(state))
    assert cache.get(settings)['ready']
    assert transitions[-1]=='passed'
    key=(str(Path(settings.data.db_path).resolve()), settings_version(settings))
    # Another audit filled the cache before a queued background worker obtained its lock.
    transitions.append('pending')
    cache._refresh(settings,key)
    assert transitions[-1]=='passed'
