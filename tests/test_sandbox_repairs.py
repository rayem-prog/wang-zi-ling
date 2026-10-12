from threading import Event
import time


def test_status_does_not_rescan_history(monkeypatch):
    import strategy.sandbox as module
    marker = object()
    monkeypatch.setattr(module, '_active_sandbox', marker)
    monkeypatch.setattr(module, 'seed_historical_market_data', lambda: (_ for _ in ()).throw(AssertionError('repeated full audit')))
    assert module.get_or_create_sandbox() is marker


def test_background_job_returns_immediately_and_deduplicates():
    from api.sandbox_jobs import SandboxJobRegistry
    gate = Event()
    started = Event()
    calls = []
    def work(progress):
        calls.append(1)
        started.set()
        progress('loading', 2, 10)
        assert gate.wait(5)
        return {'current_step': 10}
    jobs = SandboxJobRegistry()
    try:
        first = jobs.start('request-one', 'init', work)
        assert first['status'] in ('queued', 'running')
        assert started.wait(2)
        same = jobs.start('request-one', 'init', work)
        assert same['job_id'] == first['job_id']
        assert jobs.get(first['job_id'])['progress']['completed'] == 2
        gate.set()
        deadline = time.monotonic() + 3
        while jobs.get(first['job_id'])['status'] not in ('succeeded', 'failed') and time.monotonic() < deadline:
            time.sleep(.01)
        assert jobs.get(first['job_id'])['result'] == {'current_step': 10}
        assert len(calls) == 1
    finally:
        gate.set()
        jobs.close()


def test_background_failure_is_visible_and_can_retry():
    from api.sandbox_jobs import SandboxJobRegistry
    jobs = SandboxJobRegistry()
    try:
        first = jobs.start('bad', 'init', lambda progress: 1 / 0)
        deadline = time.monotonic() + 3
        while jobs.get(first['job_id'])['status'] not in ('succeeded', 'failed') and time.monotonic() < deadline:
            time.sleep(.01)
        assert jobs.get(first['job_id'])['status'] == 'failed'
        assert 'division' in jobs.get(first['job_id'])['error']
        second = jobs.start('retry', 'init', lambda progress: {'status': 'ready'})
        assert second['job_id'] != first['job_id']
    finally:
        jobs.close()


def test_evidence_uses_only_observed_history():
    import pandas as pd
    from services.plan_evidence import observed_evidence
    frame = pd.DataFrame([
        dict(code='000001', date='2026-10-08', close=10., ret_5=.1, ret_20=.2, vol_20=.03, rsi_14=60.),
        dict(code='000001', date='2026-10-09', close=999., ret_5=99., ret_20=99., vol_20=99., rsi_14=99.)])
    item = observed_evidence(frame, '2026-10-08', ['000001'])[0]
    assert item['close'] == 10.
    assert item['ret_20_pct'] == 20.
    assert item['history_end'] == '2026-10-08'


def test_personal_plan_reads_current_holdings_each_time(monkeypatch):
    from dataclasses import replace
    import pandas as pd
    from config.settings import Settings
    from services import personal_plan
    from data import db
    from strategy import holdings
    s = replace(Settings(), notify=replace(Settings().notify, backend='mock'))
    monkeypatch.setattr(personal_plan, 'load_settings', lambda: s)
    monkeypatch.setattr(personal_plan.health_cache, 'get', lambda settings, **kwargs: dict(ready=True,trade_date='2026-10-09',eligible_codes=['000001'],source='fixture',coverage=1.))
    dates = pd.bdate_range('2026-06-01', periods=80).date
    bars = pd.DataFrame(dict(code='000001', date=dates, open=10.,high=10.1,low=9.9,close=10.,volume=100.,amount=1000.))
    bars.loc[len(bars)-1,'date'] = pd.Timestamp('2026-10-09').date()
    monkeypatch.setattr(db, 'load_training_bars', lambda *args, **kwargs: bars.copy())
    first = personal_plan.build_personal_plan()
    holdings.save_positions(s.data.db_path, pd.DataFrame([dict(code='000001',name='Test',shares=1000.,cost=12.)]))
    second = personal_plan.build_personal_plan()
    assert first['holdings'] == []
    assert second['holdings'][0]['shares'] == 1000.
    assert any(o['action'] == '清仓' and o['shares'] == 1000 for o in second['orders'])
    assert second['trade_date'] == '2026-10-09'
    assert second['decision']['input_digest'] != first['decision']['input_digest']


def test_job_api_does_not_block_and_rejects_conflicting_submission(monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app
    import api.sandbox_jobs as registry
    import services.personal_plan as plans
    from api.sandbox_jobs import SandboxJobRegistry
    gate = Event()
    jobs = SandboxJobRegistry()
    monkeypatch.setattr(registry, 'sandbox_jobs', jobs)
    def slow(progress):
        assert gate.wait(5)
        return {'status':'ready','orders':[]}
    monkeypatch.setattr(plans, 'build_personal_plan', slow)
    try:
        client = TestClient(app)
        response = client.post('/api/sandbox/jobs',json={'request_id':'api-1','operation':'personal-plan'})
        assert response.status_code == 200
        job_id = response.json()['job_id']
        assert response.json()['status'] in ('queued','running')
        assert client.get('/api/sandbox/jobs/current').json()['job_id'] == job_id
        assert client.post('/api/sandbox/jobs',json={'request_id':'api-1','operation':'personal-plan'}).json()['job_id'] == job_id
        assert client.post('/api/sandbox/jobs',json={'request_id':'api-2','operation':'init'}).status_code == 409
        gate.set()
        deadline = time.monotonic()+3
        while jobs.get(job_id)['status'] not in ('succeeded','failed') and time.monotonic()<deadline:
            time.sleep(.01)
        assert client.get('/api/sandbox/jobs/'+job_id).json()['result']['status'] == 'ready'
        assert client.get('/api/sandbox/jobs/missing').status_code == 404
    finally:
        gate.set()
        jobs.close()


def test_unverified_latest_data_blocks_personal_plan(monkeypatch):
    from services import personal_plan
    monkeypatch.setattr(personal_plan.health_cache, 'get', lambda _, **kwargs: {'ready':False,'reason':'行情存在缺口'})
    result = personal_plan.build_personal_plan()
    assert result['status'] == 'blocked'
    assert result['reason'] == '行情存在缺口'
    assert result['orders'] == []


def test_daily_health_audit_can_finish_across_an_ordinary_minute(monkeypatch):
    import datetime as dt
    import api.health_cache as module
    from config.settings import Settings
    times = iter([dt.datetime(2026,10,11,19,38,55), dt.datetime(2026,10,11,19,39,3)])
    monkeypatch.setattr(module, 'exchange_now', lambda: next(times))
    cache = module.HealthCache(audit=lambda _: {'ready':True,'trade_date':'2026-10-09'},signature=lambda _: ('same-market',))
    result = cache.get(Settings())
    assert result['ready'] is True, result['reason']


def test_daily_health_finished_minute_is_cached(monkeypatch):
    import datetime as dt
    import api.health_cache as module
    from config.settings import Settings
    times = iter([dt.datetime(2026,10,11,19,38,55),dt.datetime(2026,10,11,19,39,3),dt.datetime(2026,10,11,19,39,5)])
    monkeypatch.setattr(module,'exchange_now',lambda: next(times))
    calls=[]
    def audit(_): calls.append(1); return {'ready':True}
    cache=module.HealthCache(audit=audit,signature=lambda _: 'stable')
    assert cache.get(Settings())['ready']
    assert cache.get(Settings())['ready']
    assert len(calls)==1


def test_health_rejects_changed_complete_session(monkeypatch):
    import datetime as dt
    import api.health_cache as module
    from config.settings import Settings
    times=iter([dt.datetime(2026,10,9,16,9,55),dt.datetime(2026,10,9,16,10,3)])
    monkeypatch.setattr(module,'exchange_now',lambda: next(times))
    cache=module.HealthCache(audit=lambda _: {'ready':True},signature=lambda _: 'stable')
    assert not cache.get(Settings())['ready']


def test_intraday_factor_audit_rejects_minute_crossing(monkeypatch):
    import datetime as dt
    from dataclasses import replace
    import api.health_cache as module
    from config.settings import Settings
    times=iter([dt.datetime(2026,10,9,10,0,55),dt.datetime(2026,10,9,10,1,3)])
    monkeypatch.setattr(module,'exchange_now',lambda: next(times))
    settings=replace(Settings(),engine=replace(Settings().engine,use_intraday_factors=True))
    cache=module.HealthCache(audit=lambda _: {'ready':True},signature=lambda _: 'stable')
    assert not cache.get(settings)['ready']


def test_health_rejects_changed_market_during_audit():
    import api.health_cache as module
    from config.settings import Settings
    versions=iter(['before','after'])
    cache=module.HealthCache(audit=lambda _: {'ready':True},signature=lambda _: next(versions))
    assert not cache.get(Settings())['ready']


def test_incomplete_universe_can_review_verified_holdings_but_never_add_exposure(monkeypatch):
    import pandas as pd
    from config.settings import Settings
    from services import personal_plan
    from data import db
    from strategy import holdings
    monkeypatch.setattr(personal_plan, 'load_settings', lambda: Settings())
    health=dict(ready=False,reason='部分股票历史缺口',trade_date='2026-10-09',trusted_trade_date='2026-10-09',eligible_codes=['000001'],trusted_codes=['000001'],trusted_history_codes=['000001'],source='fixture',coverage=1.)
    monkeypatch.setattr(personal_plan.health_cache,'get',lambda settings, **kwargs: health)
    dates=pd.bdate_range('2026-06-01',periods=80).date
    bars=pd.DataFrame(dict(code='000001',date=dates,open=10.,high=10.1,low=9.9,close=10.,volume=100.,amount=1000.))
    bars.loc[len(bars)-1,'date']=pd.Timestamp('2026-10-09').date()
    monkeypatch.setattr(db,'load_training_bars',lambda *args, **kwargs: bars.copy())
    holdings.save_positions('data/market.db',pd.DataFrame([dict(code='000001',name='Test',shares=1000.,cost=12.)]))
    result=personal_plan.build_personal_plan()
    assert result['status']=='limited'
    assert result['new_entries_allowed'] is False
    assert result['holdings'][0]['shares']==1000.
    assert any(o['action']=='清仓' for o in result['orders'])
    assert not any(o['action'] in ('买入','加仓') for o in result['orders'])
    assert all(row['suggested_weight']==0 for row in result['plan'])
