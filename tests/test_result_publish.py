"""Updating must preserve the previously published artifacts until a complete commit."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from config.settings import Settings
from scripts import daily_update


def test_feature_failure_preserves_previous_recommendation_metadata(tmp_path, monkeypatch):
    settings = replace(Settings(), paths=replace(Settings().paths, artifact_dir=str(tmp_path/'results')))
    folder=Path(settings.paths.artifact_dir)
    folder.mkdir()
    before=json.dumps({'trade_date':'2026-10-09','source':'fixture','generated_at':'2026-10-09T16:20:00+08:00','coverage':1})
    metadata=folder/'latest_recommendations.meta.json'
    metadata.write_text(before)
    csv=folder/'recommendations.csv'
    csv.write_text('code,name,price,score_blend\n000001,Previous,10,80\n')
    monkeypatch.setattr(daily_update.db,'list_codes',lambda _:['000001'])
    monkeypatch.setattr(daily_update,'assess_data_health',lambda *a,**k:{'ready':True,'trade_date':'2026-10-09','eligible_codes':['000001']})
    def fail(*args):
        raise RuntimeError('controlled feature failure')
    monkeypatch.setattr(daily_update,'_features_for_all',fail)
    with pytest.raises(RuntimeError,match='controlled feature failure'):
        daily_update.run_daily(settings,fast_mode=True,send_notification=False)
    assert metadata.exists(), 'Updating removed the last published metadata before it had new results'
    assert metadata.read_text()==before
    assert csv.read_text()=='code,name,price,score_blend\n000001,Previous,10,80\n'

import datetime as dt
import pandas as pd
from data import db
from data.trading_calendar import expected_sessions
from services.result_store import ResultStore
from config.settings import settings_version


@pytest.fixture
def real_daily(tmp_path):
    settings = replace(Settings(), data=replace(Settings().data, db_path=str(tmp_path/'market.db'), start_date='2026-01-01'),
                       paths=replace(Settings().paths, artifact_dir=str(tmp_path/'results')),
                       notify=replace(Settings().notify, backend='mock'))
    db.init_db(settings.data.db_path)
    days = expected_sessions('2026-01-01', '2026-06-30')[:70]
    for code, adjustment, base in [('000001', 'qfq', 10.), ('sh000300', 'none', 3000.)]:
        frame = pd.DataFrame([dict(date=day, open=base+i*.1, high=base+i*.1+.2, low=base+i*.1-.1,
                                   close=base+i*.1+.1, volume=1000., amount=10000.) for i, day in enumerate(days)])
        db.publish_bar_snapshot(settings.data.db_path, code, frame, source='test_fixture', adjustment=adjustment,
                                adjustment_version='v1', evidence='test://fixture', reason='isolated fixture')
    return settings, dt.datetime.fromisoformat(days[-1]+'T17:00:00+08:00')


def test_real_daily_publishes_one_consistent_snapshot(real_daily):
    settings, now = real_daily
    stages = []
    summary = daily_update.run_daily(settings, fast_mode=True, now=now, send_notification=False, progress=stages.append)
    view = ResultStore(settings.paths.artifact_dir).read_view()
    assert view['snapshot'] is not None
    assert len(view['snapshot']['rows']) == summary['rec_rows']
    assert view['snapshot']['versions']['parameters'] == settings_version(settings)
    assert view['audit']['data_version'] == view['snapshot']['versions']['data']
    assert 0 < (dt.datetime.fromisoformat(view['audit']['valid_until'])-dt.datetime.fromisoformat(view['audit']['checked_at'])).total_seconds() <= 120
    assert stages == ['audit', 'features', 'train', 'score', 'risk', 'validate', 'persist', 'publish', 'complete']
    assert view['snapshot']['selection']['funnel'][0]['input'] == summary['scores_rows']


@pytest.mark.parametrize('stage', ['features', 'train', 'validate', 'persist', 'publish'])
def test_early_failure_preserves_published_head_and_files(real_daily, stage, monkeypatch):
    settings, now = real_daily
    daily_update.run_daily(settings, fast_mode=True, now=now, send_notification=False)
    store = ResultStore(settings.paths.artifact_dir)
    before = store.read_view()['snapshot']
    artifacts = Path(settings.paths.artifact_dir)
    files = {p.name: p.read_bytes() for p in artifacts.glob('*.csv')}
    files['latest_recommendations.meta.json'] = (artifacts/'latest_recommendations.meta.json').read_bytes()
    if stage == 'train':
        monkeypatch.setattr(daily_update.PredictionLedger, 'get', lambda *args: None)
    def fail(current):
        if current == stage:
            raise RuntimeError('controlled '+stage)
    with pytest.raises(RuntimeError, match='controlled'):
        daily_update.run_daily(settings, fast_mode=True, now=now, send_notification=False, progress=fail)
    assert store.read_view()['snapshot'] == before
    assert all((artifacts/name).read_bytes() == content for name, content in files.items())


def test_input_change_rejects_publish(real_daily):
    import sqlite3
    settings, now = real_daily
    daily_update.run_daily(settings, fast_mode=True, now=now, send_notification=False)
    store = ResultStore(settings.paths.artifact_dir)
    old = store.read_view()['snapshot']['snapshot_id']
    def mutate(stage):
        if stage == 'publish':
            with sqlite3.connect(settings.data.db_path) as connection:
                connection.execute("UPDATE bars SET volume=volume+1 WHERE code='000001'")
    with pytest.raises(ValueError, match='行情版本'):
        daily_update.run_daily(settings, fast_mode=True, now=now, send_notification=False, progress=mutate)
    assert store.read_view()['snapshot']['snapshot_id'] == old


def test_legitimate_empty_plan_publishes(real_daily, monkeypatch):
    settings, now = real_daily
    original = daily_update.decide
    def empty(*args, **kwargs):
        decision = original(*args, **kwargs)
        decision.plan = decision.plan.iloc[:0]
        decision.orders = decision.orders.iloc[:0]
        return decision
    monkeypatch.setattr(daily_update, 'decide', empty)
    result = daily_update.run_daily(settings, fast_mode=True, now=now, send_notification=False)
    snapshot = ResultStore(settings.paths.artifact_dir).read_view()['snapshot']
    assert snapshot is not None and snapshot['rows'] == [] and result['rec_rows'] == 0


def test_changed_parameters_regenerate_served_decision_without_mutating_forecast(real_daily):
    settings, now = real_daily
    daily_update.run_daily(settings, fast_mode=True, now=now, send_notification=False)
    ledger=daily_update.PredictionLedger(settings.data.db_path)
    before=ledger.get(now.date().isoformat())
    ledger.close()
    modified = replace(settings, risk=replace(settings.risk, stop_loss=.1))
    daily_update.run_daily(modified, fast_mode=True, now=now, send_notification=False)
    snapshot = ResultStore(settings.paths.artifact_dir).read_view()['snapshot']
    assert snapshot['versions']['parameters'] == settings_version(modified)
    assert snapshot['rows'][0]['why_recommended']['stop_loss_price']==round(snapshot['rows'][0]['price']*.9,2)
    ledger=daily_update.PredictionLedger(settings.data.db_path)
    assert ledger.get(now.date().isoformat())==before
    ledger.close()


def test_legacy_forecast_gets_current_served_snapshot_without_mutation(real_daily):
    import sqlite3
    settings, now=real_daily
    daily_update.run_daily(settings, fast_mode=True, now=now, send_notification=False)
    with sqlite3.connect(settings.data.db_path) as conn:
        raw=conn.execute('SELECT payload FROM prediction_snapshots WHERE date=?',(now.date().isoformat(),)).fetchone()[0]
        legacy=json.loads(raw)
        legacy['provenance'].pop('data_version')
        legacy['provenance'].pop('parameters_version')
        legacy_raw=json.dumps(legacy)
        conn.execute('UPDATE prediction_snapshots SET payload=? WHERE date=?',(legacy_raw,now.date().isoformat()))
    daily_update.run_daily(settings, fast_mode=True, now=now, send_notification=False)
    view=ResultStore(settings.paths.artifact_dir).read_view()
    assert view['snapshot']['versions']['parameters']==settings_version(settings)
    assert view['snapshot']['versions']['data']==view['audit']['data_version']
    with sqlite3.connect(settings.data.db_path) as conn:
        assert conn.execute('SELECT payload FROM prediction_snapshots WHERE date=?',(now.date().isoformat(),)).fetchone()[0]==legacy_raw


def test_report_failure_after_publish_is_auxiliary_warning(real_daily, monkeypatch):
    settings, now = real_daily
    monkeypatch.setattr(daily_update.recap, 'generate_daily_recap', lambda **kwargs: (_ for _ in ()).throw(RuntimeError('report offline')))
    result = daily_update.run_daily(settings, fast_mode=True, now=now, send_notification=False)
    assert ResultStore(settings.paths.artifact_dir).read_view()['snapshot'] is not None
    assert not result.get('blocked') and 'report offline' in str(result['warnings'])


def test_direct_generation_cannot_bypass_active_process_lease(real_daily):
    from api.jobs import GenerationLease
    settings, now=real_daily
    lease=GenerationLease(settings.paths.artifact_dir)
    assert lease.acquire()
    try:
        result=daily_update.run_daily(settings, fast_mode=True, now=now, send_notification=False)
        assert result['blocked'] is True and '正在' in result['error']
        assert ResultStore(settings.paths.artifact_dir).read_view()['snapshot'] is None
    finally:
        lease.release()


def test_sync_generation_invalidates_current_audit_before_fetch(real_daily, monkeypatch):
    """A direct sync/CLI run must pause a published result during input mutation."""
    from threading import Event, Thread
    from fastapi.testclient import TestClient
    from api.main import app
    from api.routers import signals as signals_router
    from services import signals as signals_service

    settings, now = real_daily
    daily_update.run_daily(settings, fast_mode=True, now=now, send_notification=False)
    store = ResultStore(settings.paths.artifact_dir)
    before = store.read_view()
    assert before['snapshot'] is not None
    assert before['audit']['state'] == 'passed'
    monkeypatch.setattr(signals_router, 'load_settings', lambda: settings)
    monkeypatch.setattr(signals_service, 'exchange_now', lambda: now)

    entered = Event()
    release = Event()
    def blocked_update(_settings):
        entered.set()
        assert release.wait(5), 'controlled pipeline was not released'
        return {'requested_codes': ['000001']}
    monkeypatch.setattr(daily_update.pipeline, 'update_all', blocked_update)
    worker = Thread(target=lambda: daily_update.run_daily(
        settings, fast_mode=False, now=now, send_notification=False))
    worker.start()
    try:
        assert entered.wait(5), 'sync generation did not reach fetch'
        response = TestClient(app).get('/api/signals/snapshot')
        assert response.status_code == 200
        body = response.json()
        assert body['result_state'] == 'historical'
        assert body['action_permissions']['create_buy'] == 'paused'
        assert body['audit_state'] in {'pending', 'running'}
        assert body['snapshot_id'] == before['snapshot']['snapshot_id']
        assert body['rows']
    finally:
        release.set()
        worker.join(timeout=10)
    assert not worker.is_alive()


def test_sync_generation_remains_paused_if_health_cache_finishes_during_fetch(real_daily, monkeypatch):
    """A concurrent cache audit must not restore currentness while CLI owns the lease."""
    from threading import Event, Thread
    from fastapi.testclient import TestClient
    from api.main import app
    from api.routers import signals as signals_router
    from services import signals as signals_service
    from services.result_pipeline import record_health_projection

    settings, now = real_daily
    daily_update.run_daily(settings, fast_mode=True, now=now, send_notification=False)
    store = ResultStore(settings.paths.artifact_dir)
    before = store.read_view()
    assert before['snapshot'] is not None and before['audit']['state'] == 'passed'
    monkeypatch.setattr(signals_router, 'load_settings', lambda: settings)
    monkeypatch.setattr(signals_service, 'exchange_now', lambda: now)

    entered = Event()
    release = Event()
    def blocked_update(_settings):
        entered.set()
        assert release.wait(5), 'controlled pipeline was not released'
        return {'requested_codes': ['000001']}
    monkeypatch.setattr(daily_update.pipeline, 'update_all', blocked_update)
    worker = Thread(target=lambda: daily_update.run_daily(
        settings, fast_mode=False, now=now, send_notification=False))
    worker.start()
    try:
        assert entered.wait(5), 'sync generation did not reach fetch'
        # Model the health-cache worker completing from the still-unchanged old
        # market rows after the direct generator marked the prior certificate pending.
        projection = record_health_projection(settings, before['audit']['health'], 'passed', now=now)
        assert projection['ready'] is True
        response = TestClient(app).get('/api/signals/snapshot')
        assert response.status_code == 200
        body = response.json()
        assert body['result_state'] == 'historical'
        assert body['action_permissions']['create_buy'] == 'paused'
        assert body['snapshot_id'] == before['snapshot']['snapshot_id']
        assert body['rows']
    finally:
        release.set()
        worker.join(timeout=10)
    assert not worker.is_alive()


def test_published_job_has_success_result_before_status_turns_succeeded(real_daily, monkeypatch):
    """Job polling must not observe succeeded with an empty result envelope."""
    from threading import Event
    from api import jobs

    settings, now = real_daily
    monkeypatch.setattr(jobs, 'load_settings', lambda: settings)
    original_run = jobs.daily_update.run_daily
    monkeypatch.setattr(jobs.daily_update, 'run_daily',
        lambda **kwargs: original_run(now=now, **kwargs))
    published = Event()
    release = Event()
    original_publish = ResultStore.publish
    def publish_then_pause(self, *args, **kwargs):
        revision = original_publish(self, *args, **kwargs)
        published.set()
        assert release.wait(5), 'controlled post-publish pause was not released'
        return revision
    monkeypatch.setattr(ResultStore, 'publish', publish_then_pause)
    registry = jobs.DailyJobRegistry()
    job = registry.start(True, send_notification=False)
    try:
        assert published.wait(15), 'generation did not publish its snapshot'
        envelope = registry.get(job['job_id'])
        assert envelope['status'] == 'succeeded'
        assert envelope.get('result') is not None
        assert envelope['result'].get('success') is True
        assert envelope['result'].get('result', {}).get('snapshot_id')
    finally:
        release.set()
    registry._pool.shutdown(wait=True)
