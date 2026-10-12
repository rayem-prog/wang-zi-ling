"""Snapshot HTTP reads never touch the market or mutable CSV artifacts."""
from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from config.settings import Settings, settings_version
from services.result_store import ResultStore
from api.main import app

NOW = datetime.fromisoformat('2026-10-12T10:00:00+08:00')

@pytest.fixture
def setup_snapshot(tmp_path, monkeypatch):
    from api.routers import signals
    from services import signals as service
    settings = replace(Settings(), paths=replace(Settings().paths, artifact_dir=str(tmp_path/'results')))
    monkeypatch.setattr(signals, 'load_settings', lambda: settings)
    monkeypatch.setattr(service, 'exchange_now', lambda: NOW, raising=False)
    return settings, ResultStore(settings.paths.artifact_dir), TestClient(app)


def payload(settings, rows=None):
    return dict(rows=[dict(code='000001', name='One', price=10., score_blend=80., price_bracket='low', rank=1),
                      dict(code='000002', name='Two', price=50., score_blend=70., price_bracket='mid', rank=2)] if rows is None else rows,
                orders=[], trade_date='2026-10-09', data_as_of='2026-10-09T15:00:00+08:00', generated_at='2026-10-09T17:00:00+08:00',
                versions=dict(data='d1', strategy='s1', parameters=settings_version(settings)), valid_until='2026-10-12T16:10:00+08:00',
                allow_new_buy=True, selection={'funnel': []})


def publish(settings, store, rows=None):
    store.publish(store.stage(payload(settings, rows)))
    store.record_audit(dict(state='passed', ready=True, data_version='d1', parameters_version=settings_version(settings),
                            checked_at='2026-10-12T09:59:00+08:00', valid_until='2026-10-12T10:01:00+08:00', error=None))


def test_snapshot_none_is_read_only_complete_json(setup_snapshot):
    settings, store, client = setup_snapshot
    response = client.get('/api/signals/snapshot')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['empty_kind'] == 'no_snapshot'
    assert response.json()['job']['status'] == 'idle'
    assert not store.path.parent.exists()


def test_snapshot_read_calls_no_audit_history_training_fetch_or_csv(setup_snapshot, monkeypatch):
    from api import health_cache
    from scripts import daily_update
    from services import recommendations
    settings, store, client = setup_snapshot
    publish(settings, store)
    def forbidden(*args, **kwargs):
        raise AssertionError('expensive dependency used on GET')
    monkeypatch.setattr(health_cache, 'market_signature', forbidden)
    monkeypatch.setattr(health_cache.health_cache, 'get_background', forbidden)
    monkeypatch.setattr(daily_update, 'assess_data_health', forbidden)
    monkeypatch.setattr(daily_update, '_features_for_all', forbidden)
    monkeypatch.setattr(daily_update.pipeline, 'update_all', forbidden)
    monkeypatch.setattr(recommendations.pd, 'read_csv', forbidden)
    calls = []
    original = ResultStore.read_view
    def read(self):
        calls.append(1)
        return original(self)
    monkeypatch.setattr(ResultStore, 'read_view', read)
    response = client.get('/api/signals/snapshot?bracket=mid&sort_by=price_desc&q=two')
    assert response.status_code == 200 and len(calls) == 1
    body = response.json()
    assert body['result_state'] == 'current'
    assert body['counts']['snapshot_total'] == 2 and body['counts']['filtered_total'] == 1
    assert body['rows'][0]['rank'] == 2


@pytest.mark.parametrize('query', ['bracket=bad', 'min_price=-1', 'min_price=nan', 'min_price=0', 'min_price=50&max_price=20', 'sort_by=bad', 'codes=oops', 'codes=000001,', 'q='+'x'*201])
def test_malformed_snapshot_filters_are_422(setup_snapshot, query):
    assert setup_snapshot[2].get('/api/signals/snapshot?'+query).status_code == 422


def test_empty_result_and_filter_no_match_are_distinct(setup_snapshot):
    settings, store, client = setup_snapshot
    publish(settings, store, [])
    assert client.get('/api/signals/snapshot').json()['empty_kind'] == 'screened_out'
    publish(settings, store)
    body = client.get('/api/signals/snapshot?codes=000099').json()
    assert body['empty_kind'] == 'filter_no_match' and body['counts']['snapshot_total'] == 2


def test_pending_failed_audit_keeps_rows_and_structured_job_error(setup_snapshot):
    settings, store, client = setup_snapshot
    publish(settings, store)
    for status in ('pending', 'failed'):
        store.record_audit(dict(state=status, ready=False, data_version='d1', parameters_version=settings_version(settings),
                                checked_at='2026-10-12T09:59:00+08:00', valid_until=None,
                                error=dict(code='audit_failed', message='offline', retryable=True) if status=='failed' else None))
        store.save_job(dict(job_id='j1', status='failed', error='offline'))
        body = client.get('/api/signals/snapshot').json()
        assert body['result_state'] == 'historical' and len(body['rows']) == 2
        assert body['job']['error']['message'] == 'offline'


def test_active_data_refresh_pauses_current_snapshot_before_audit(setup_snapshot):
    settings, store, client = setup_snapshot
    publish(settings, store)
    store.save_job(dict(job_id='refresh-1', status='running', stage='fetch', data_only=True,
                        created_at='2026-10-12T09:59:00+08:00', started_at='2026-10-12T09:59:01+08:00'))
    body=client.get('/api/signals/snapshot').json()
    assert body['result_state']=='historical'
    assert body['action_permissions']['create_buy']=='paused'
    assert len(body['rows'])==2


def test_system_status_separates_valid_market_audit_from_legacy_result_history(setup_snapshot, monkeypatch):
    from api.routers import status
    settings, store, client = setup_snapshot
    current=datetime.now().astimezone()
    legacy = payload(settings)
    legacy['legacy_import'] = True
    store.publish(store.stage(legacy))
    store.record_audit(dict(state='passed', ready=True, data_version='d1',
        parameters_version=settings_version(settings), checked_at=current.isoformat(),
        valid_until=(current+timedelta(minutes=2)).isoformat(), error=None,
        health=dict(ready=True, trade_date='2026-10-09', source='verified_daily')))
    monkeypatch.setattr(status, 'load_settings', lambda: settings)
    monkeypatch.setattr(status, 'exchange_now', lambda: current)
    def forbidden(*args, **kwargs):
        raise AssertionError('status GET must use persisted health evidence')
    monkeypatch.setattr(status.health_cache, 'get', forbidden)
    body=client.get('/api/status').json()
    assert body['data_health']['ready'] is True
    assert body['recommendation']['ready'] is False


def test_corrupt_store_returns_503(setup_snapshot):
    settings, store, client = setup_snapshot
    store.path.parent.mkdir()
    store.path.write_bytes(b'corrupted database')
    response = client.get('/api/signals/snapshot')
    assert response.status_code == 503 and response.json()['error']['code'] == 'result_store_unavailable'


def test_bootstrap_valid_legacy_only_imports_as_history(setup_snapshot):
    from services.result_pipeline import bootstrap_results
    settings, store, client = setup_snapshot
    store.path.parent.mkdir()
    (store.path.parent/'recommendations.csv').write_text('code,name,price,score_blend\n000001,One,10,80\n')
    (store.path.parent/'latest_recommendations.meta.json').write_text(json.dumps(dict(trade_date='2026-10-09', source='verified_daily', generated_at='2026-10-09T16:20:00+08:00', coverage=1., history_coverage=1., updated_at='2026-10-09T16:00:00+08:00', price_source='daily_bar_close')))
    bootstrap_results(settings)
    first = client.get('/api/signals/snapshot').json()
    assert first['result_state'] == 'historical' and len(first['rows']) == 1
    bootstrap_results(settings)
    assert client.get('/api/signals/snapshot').json()['snapshot_id'] == first['snapshot_id']


@pytest.mark.parametrize('metadata', [{}, {'trade_date':'2026-10-09','source':'simulation','generated_at':'2026-10-09T16:20:00+08:00','coverage':1}])
def test_invalid_legacy_remains_none_with_reason(setup_snapshot, metadata):
    from services.result_pipeline import bootstrap_results
    settings, store, client = setup_snapshot
    store.path.parent.mkdir()
    (store.path.parent/'recommendations.csv').write_text('code,name,price,score_blend\n000001,One,10,80\n')
    (store.path.parent/'latest_recommendations.meta.json').write_text(json.dumps(metadata))
    bootstrap_results(settings)
    body=client.get('/api/signals/snapshot').json()
    assert body['result_state']=='none' and body['audit']['error']['code']=='legacy_import_failed'


def test_health_cache_persists_only_consistent_fresh_audit_and_failure_context(setup_snapshot, monkeypatch):
    from api import health_cache as module
    settings, store, client = setup_snapshot
    publish(settings, store)
    monkeypatch.setattr(module, 'exchange_now', lambda: NOW)
    signature = ['market-1']; ticks = [0.]
    health = dict(ready=True, trade_date='2026-10-09', coverage=1., source='verified_daily', reason='')
    def audit(_):
        return dict(health)
    cache = module.HealthCache(audit=audit, signature=lambda _: signature[0], clock=lambda: ticks[0])
    cache.get(settings)
    cert=store.read_view()['audit']
    assert cert['state']=='passed' and cert['allow_new_buy'] is True
    assert cert['health']['source']=='verified_daily'
    assert dt_delta(cert['valid_until'],cert['checked_at'])<=120
    ticks[0]=121
    def fail(_):
        raise RuntimeError('provider offline')
    cache.audit=fail
    result=cache.get(settings)
    view=store.read_view()
    assert not result['ready'] and view['audit']['state']=='failed'
    assert view['audit']['source']=='verified_daily' and view['audit']['trade_date']=='2026-10-09'
    assert view['audit']['error']['message'].endswith('provider offline')
    assert len(client.get('/api/signals/snapshot').json()['rows'])==2


def dt_delta(first, second):
    return (datetime.fromisoformat(first)-datetime.fromisoformat(second)).total_seconds()


def test_health_audit_input_change_never_persists_passed(setup_snapshot, monkeypatch):
    from api import health_cache as module
    settings, store, client=setup_snapshot
    monkeypatch.setattr(module,'exchange_now',lambda: NOW)
    signature=['before']
    def audit(_):
        signature[0]='after'
        return dict(ready=True,trade_date='2026-10-09',reason='')
    cache=module.HealthCache(audit=audit,signature=lambda _:signature[0])
    assert cache.get(settings)['ready'] is False
    assert store.read_view()['audit']['state']=='failed'


def test_result_deadline_next_session_and_intraday_limit():
    from services.result_pipeline import audit_deadline
    settings=Settings()
    now=datetime.fromisoformat('2026-10-09T17:00:00+08:00')
    assert audit_deadline(settings,'2026-10-09',now)=='2026-10-12T16:10:00+08:00'
    intraday=replace(settings,engine=replace(settings.engine,use_intraday_factors=True))
    assert dt_delta(audit_deadline(intraday,'2026-10-09',now),now.isoformat())<=300
    assert audit_deadline(settings,'2026-12-31',now) is None


def test_legacy_optional_infinite_numeric_field_is_rejected(setup_snapshot):
    from services.result_pipeline import bootstrap_results
    settings,store,client=setup_snapshot
    store.path.parent.mkdir()
    (store.path.parent/'recommendations.csv').write_text('code,name,price,score_blend,score_a\n000001,One,10,80,inf\n')
    (store.path.parent/'latest_recommendations.meta.json').write_text(json.dumps(dict(trade_date='2026-10-09', source='verified_daily', generated_at='2026-10-09T16:20:00+08:00', coverage=1., history_coverage=1., updated_at='2026-10-09T16:00:00+08:00', price_source='daily_bar_close')))
    bootstrap_results(settings)
    body=client.get('/api/signals/snapshot').json()
    assert body['result_state']=='none' and body['audit']['error']['code']=='legacy_import_failed'
