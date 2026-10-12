"""A visible historical or revoked ranking must never bypass execution checks."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.main import app
from config.settings import Settings, settings_version
from services.result_store import ResultStore
from strategy import paper


@pytest.fixture
def published(tmp_path,monkeypatch):
    from api.routers import paper as route, orders
    settings=replace(Settings(),paths=replace(Settings().paths,artifact_dir=str(tmp_path/'artifacts')))
    folder=Path(settings.paths.artifact_dir);folder.mkdir()
    now=datetime.now().astimezone()
    from data.health import _expected_session
    day=_expected_session(now).isoformat()
    pd.DataFrame([dict(code='000001',name='Fixture',price=10,score_blend=80)]).to_csv(folder/'recommendations.csv',index=False)
    (folder/'latest_recommendations.meta.json').write_text(json.dumps(dict(trade_date=day,generated_at=now.isoformat(),source='test_fixture',coverage=1)))
    row=dict(code='000001',name='Fixture',price=10,score_blend=80,price_bracket='low',rank=1,hand_cost=1000)
    payload=dict(rows=[row],orders=[],data_as_of=now.isoformat(),generated_at=now.isoformat(),trade_date=day,
       versions=dict(data='fixture-v1',strategy='strategy-v1',parameters=settings_version(settings)),
       valid_until=(now+timedelta(hours=1)).isoformat(),allow_new_buy=True,selection=dict(funnel=[]))
    store=ResultStore(folder);snapshot=store.stage(payload);store.publish(snapshot)
    store.record_audit(dict(state='passed',data_version='fixture-v1',parameters_version=settings_version(settings),checked_at=now.isoformat(),valid_until=(now+timedelta(minutes=2)).isoformat(),ready=True,error=None,allow_new_buy=True))
    monkeypatch.setattr(route,'load_settings',lambda:settings)
    monkeypatch.setattr(orders,'load_settings',lambda:settings)
    monkeypatch.setattr(paper,'load_settings',lambda:settings)
    health=dict(ready=True,trade_date=day,training_ready=True,coverage=1,reason='')
    monkeypatch.setattr(route,'assess_data_health',lambda _:health)
    monkeypatch.setattr(orders,'assess_data_health',lambda _:health)
    return settings,store,snapshot,health


def test_revoked_snapshot_blocks_ai_execution_before_account_changes(published):
    settings,store,snapshot,_=published
    store.revoke(snapshot,dict(code='SOURCE_REVOKED',message='输入来源已撤销'))
    response=TestClient(app).post('/api/paper/ai-simulation/execute',json={'snapshot_id':snapshot})
    assert response.status_code==409
    assert '撤销' in str(response.json())
    assert paper.get_paper_account(str(Path(settings.paths.artifact_dir)/'paper.db'))['positions']==[]


def test_snapshot_bound_manual_buy_rejects_revoked_version(published):
    settings,store,snapshot,_=published
    store.revoke(snapshot,dict(code='SOURCE_REVOKED',message='输入来源已撤销'))
    response=TestClient(app).post('/api/paper/order',json=dict(snapshot_id=snapshot,code='000001',name='Fixture',side='BUY',shares=100,price=10))
    assert response.status_code==409
    assert paper.get_paper_account(str(Path(settings.paths.artifact_dir)/'paper.db'))['positions']==[]


def test_manual_buy_without_snapshot_rejects_revoked_published_head(published):
    settings,store,snapshot,_=published
    store.revoke(snapshot,dict(code='SOURCE_REVOKED',message='输入来源已撤销'))
    response=TestClient(app).post('/api/paper/order',json=dict(code='000001',name='Fixture',side='BUY',shares=100,price=10))
    assert response.status_code==409
    assert paper.get_paper_account(str(Path(settings.paths.artifact_dir)/'paper.db'))['positions']==[]


def test_holdings_manual_buy_without_snapshot_rejects_revoked_published_head(published,monkeypatch):
    settings,store,snapshot,health=published
    from api.routers import holdings as route
    monkeypatch.setattr(route,'load_settings',lambda:settings)
    monkeypatch.setattr(route,'assess_data_health',lambda _:health)
    store.revoke(snapshot,dict(code='SOURCE_REVOKED',message='输入来源已撤销'))
    response=TestClient(app).post('/api/holdings/paper',json=dict(code='000001',shares=100,price=10,side='BUY'))
    assert response.status_code==409
    assert paper.get_paper_account(str(Path(settings.paths.artifact_dir)/'paper.db'))['positions']==[]


def test_export_rejects_revoked_snapshot_even_when_legacy_files_are_present(published):
    settings,store,snapshot,_=published
    pd.DataFrame([dict(code='000001',name='Fixture',action='买入',shares=100,limit_low=9,limit_high=11)]).to_csv(Path(settings.paths.artifact_dir)/'latest_orders.csv',index=False)
    store.revoke(snapshot,dict(code='SOURCE_REVOKED',message='输入来源已撤销'))
    response=TestClient(app).get('/api/orders/export')
    assert response.status_code==409


def test_ai_execution_rejects_superseded_snapshot_id(published):
    _,store,snapshot,_=published
    value=deepcopy(store.read_view()['snapshot']);value.pop('snapshot_id');value['versions']['data']='fixture-v2'
    store.publish(store.stage(value))
    response=TestClient(app).post('/api/paper/ai-simulation/execute',json={'snapshot_id':snapshot})
    assert response.status_code==409


def test_ai_plan_uses_published_rows_even_if_compatibility_csv_changes(published,monkeypatch):
    settings,_,snapshot,_=published
    from services import decision_results
    monkeypatch.setattr(decision_results,'current_data_version',lambda _:'fixture-v1')
    pd.DataFrame([dict(code='000002',name='Mutable CSV',price=100,score_blend=99)]).to_csv(Path(settings.paths.artifact_dir)/'recommendations.csv',index=False)
    response=TestClient(app).get('/api/paper/ai-simulation',params={'snapshot_id':snapshot})
    assert response.status_code==200
    plan=response.json()
    assert plan['snapshot_id']==snapshot
    assert [row['code'] for row in plan['recommendations']]==['000001']


def test_current_snapshot_permissions_follow_latest_certified_risk(published):
    settings,store,_,_=published
    from services.result_contract import build_snapshot_response
    now=datetime.now().astimezone()
    current=build_snapshot_response(store.read_view(),settings,now=now)
    assert current['action_permissions']['create_buy']=='allowed'
    audit=store.read_view()['audit'];audit['allow_new_buy']=False;store.record_audit(audit)
    restricted=build_snapshot_response(store.read_view(),settings,now=now)
    assert restricted['action_permissions']['create_buy']=='forbidden'
    assert restricted['action_permissions']['add_position']=='forbidden'
    assert len(restricted['rows'])==1


def test_bound_manual_buy_pauses_when_generation_job_is_active(published,monkeypatch):
    settings,store,snapshot,_=published
    from services import decision_results
    monkeypatch.setattr(decision_results,'current_data_version',lambda _:'fixture-v1')
    store.save_job(dict(job_id='generation-in-progress',status='running',stage='fetch',
                        data_only=False,started_at=datetime.now().astimezone().isoformat()))
    response=TestClient(app).post('/api/paper/order',json=dict(snapshot_id=snapshot,code='000001',
        name='Fixture',side='BUY',shares=100,price=10))
    assert response.status_code==409
    assert paper.get_paper_account(str(Path(settings.paths.artifact_dir)/'paper.db'))['positions']==[]


def test_rejected_legacy_bootstrap_cannot_fall_back_to_tradeable_csv(tmp_path):
    settings=replace(Settings(),data=replace(Settings().data,db_path=str(tmp_path/'market.db')),
                     paths=replace(Settings().paths,artifact_dir=str(tmp_path/'artifacts')))
    folder=Path(settings.paths.artifact_dir)
    folder.mkdir(parents=True)
    now=datetime.now().astimezone()
    day='2026-10-09'
    pd.DataFrame([dict(code='000001',name='Unverified legacy',price=10,score_blend=90)]).to_csv(
        folder/'recommendations.csv',index=False)
    # Startup migration requires all provenance fields; the legacy runtime
    # fallback historically accepts this incomplete metadata.
    (folder/'latest_recommendations.meta.json').write_text(json.dumps(dict(
        trade_date=day,generated_at=now.isoformat(),source='fixture',coverage=1)))
    from services.result_pipeline import bootstrap_results
    from data.health import recommendation_state
    bootstrap_results(settings)
    view=ResultStore(settings.paths.artifact_dir).read_view()
    assert view['snapshot'] is None
    assert view['audit']['state']=='failed'
    store=ResultStore(settings.paths.artifact_dir)
    store.record_audit(dict(state='passed',ready=True,data_version='fixture-data',
        parameters_version=settings_version(settings),checked_at=now.isoformat(),
        valid_until=(now+timedelta(minutes=2)).isoformat(),error=None,
        health={'ready':True,'trade_date':day}))
    state=recommendation_state(settings,dict(ready=True,trade_date=day,reason=''))
    assert state['ready'] is False
    assert '导入' in state['reason'] or '来源' in state['reason']


def test_parameter_change_pauses_operations_but_keeps_ranking(published):
    settings,store,_,_=published
    from services.result_contract import build_snapshot_response
    changed=replace(settings,account=replace(settings.account,cash=50000))
    result=build_snapshot_response(store.read_view(),changed,now=datetime.now().astimezone())
    assert result['result_state']=='historical'
    assert len(result['rows'])==1
    assert result['action_permissions']['create_buy']=='paused'


def test_order_listing_reads_same_published_version_not_mutable_csv(published,monkeypatch):
    settings,store,_,_=published
    from services import decision_results
    monkeypatch.setattr(decision_results,'current_data_version',lambda _:'fixture-v1')
    # The published version intentionally has no orders; a changed CSV must not add one.
    pd.DataFrame([dict(code='000002',name='Mutable CSV',action='买入',shares=100,limit_low=9,limit_high=11)]).to_csv(Path(settings.paths.artifact_dir)/'latest_orders.csv',index=False)
    response=TestClient(app).get('/api/orders')
    assert response.status_code==200
    assert response.json()==[]


def test_system_status_uses_persisted_projection_without_a_page_audit(published,monkeypatch):
    settings,_,_,_=published
    from api.routers import status
    monkeypatch.setattr(status,'load_settings',lambda:settings)
    def blocked(*args,**kwargs):
        raise AssertionError('A browser status query must not audit full history')
    monkeypatch.setattr(status.health_cache,'get',blocked)
    response=TestClient(app).get('/api/status')
    assert response.status_code==200
    assert response.json()['counts']['recommendations']==1


def test_bound_buy_rejects_a_symbol_outside_that_snapshot(published,monkeypatch):
    settings,_,snapshot,_=published
    from services import decision_results
    monkeypatch.setattr(decision_results,'current_data_version',lambda _:'fixture-v1')
    response=TestClient(app).post('/api/paper/order',json=dict(snapshot_id=snapshot,code='000999',side='BUY',shares=100,price=10))
    assert response.status_code==409
    assert paper.get_paper_account(str(Path(settings.paths.artifact_dir)/'paper.db'))['positions']==[]


def test_revoked_snapshot_does_not_block_independent_sell(published):
    settings,store,snapshot,_=published
    path=str(Path(settings.paths.artifact_dir)/'paper.db')
    paper.execute_paper_order(path,'000001','Fixture','BUY',100,10)
    store.revoke(snapshot,dict(code='REVOKED',message='已撤销'))
    response=TestClient(app).post('/api/paper/order',json=dict(snapshot_id=snapshot,code='000001',side='SELL',shares=100,price=10))
    assert response.status_code==200
    assert paper.get_paper_account(path)['positions']==[]
