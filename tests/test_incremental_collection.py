from dataclasses import replace
import datetime as dt
import json
from pathlib import Path

import pandas as pd
import pytest

from config.settings import Settings
from data import db


def bars(days, price=10):
    return pd.DataFrame([dict(date=day, open=price, high=price+1, low=price-1,
                              close=price, volume=100, amount=1000) for day in days])


def test_gap_plan_fetches_only_gaps_with_existing_anchors():
    from data.incremental import gap_plan
    existing=bars(['2026-10-08','2026-10-12'])
    plan=gap_plan(existing,'000001','2026-10-08','2026-10-13')
    assert len(plan)==1
    assert plan[0]['missing_dates']==['2026-10-09','2026-10-13']
    assert plan[0]['start']=='2026-10-08'
    assert plan[0]['end']=='2026-10-13'
    assert gap_plan(bars(['2026-10-08','2026-10-09','2026-10-12']),
                    '000001','2026-10-08','2026-10-12')==[]


def test_incremental_patch_checks_adjustment_and_keeps_existing_history():
    from data.incremental import merge_patch, AdjustmentMismatch
    old=bars(['2026-10-08','2026-10-09'])
    new=bars(['2026-10-09','2026-10-12'])
    merged=merge_patch(old,new,adjustment='qfq')
    assert merged.date.tolist()==['2026-10-08','2026-10-09','2026-10-12']
    assert merged.iloc[0].close==10
    with pytest.raises(AdjustmentMismatch):
        merge_patch(old,bars(['2026-10-09','2026-10-12'],price=11),adjustment='qfq')
    with pytest.raises(AdjustmentMismatch):
        merge_patch(old,bars(['2026-10-12']),adjustment='qfq')
    pd.testing.assert_frame_equal(old,bars(['2026-10-08','2026-10-09']))


def test_cross_provider_units_are_converted_only_with_parent_evidence(tmp_path):
    from data.incremental import align_patch_units,merge_patch
    proof=tmp_path/'parent.json';proof.write_text(json.dumps({'volume_unit':'shares, Sina native'}))
    old=bars(['2026-10-09']);old['volume']=29788714.;old['amount']=461678594.
    old['source']='sina_daily+sina_qfq_factor';old['evidence']=str(proof)
    patch=bars(['2026-10-09','2026-10-12']);patch['volume']=297887.;patch['amount']=461678600.
    patch.attrs=dict(source='tencent_newfqkline',volume_unit='provider_native',volume_quantum=1.,amount_quantum=100.)
    converted=align_patch_units(old,patch,'601699')
    assert converted.volume.tolist()==[29788700.,29788700.]
    merged=merge_patch(old,converted,adjustment='qfq')
    assert merged.iloc[0].volume==29788714.
    assert merged.iloc[1].volume==29788700.
    converted['close']=converted.close.astype(float)
    converted.loc[0,'close']=10.1
    from data.incremental import AdjustmentMismatch
    with pytest.raises(AdjustmentMismatch):merge_patch(old,converted,adjustment='qfq')
    assert align_patch_units(old,patch,'sz000001').attrs['volume_multiplier']==100
    patch.attrs['anchor_quantums']={'2026-10-09':{'volume':1,'amount':100},
                                  '2026-10-12':{'volume':1,'amount':10000}}
    patch.attrs['amount_quantum']=10000
    wrong=old.copy();wrong['amount']=461675600.
    with pytest.raises(AdjustmentMismatch):merge_patch(wrong,align_patch_units(wrong,patch,'601699'),adjustment='qfq')


def test_composite_anchor_origin_preserves_future_same_provider_rebase_check(tmp_path):
    from data.incremental import anchor_sources
    parent=tmp_path/'parent.json';parent.write_text(json.dumps(dict(source='sina_daily+sina_qfq_factor')))
    composite=tmp_path/'composite.json';composite.write_text(json.dumps(dict(kind='anchored_incremental',
        parent_evidence=[str(parent)],patches=[dict(source='tencent_newfqkline',added_dates=['2026-10-12'])])))
    base=bars(['2026-10-09','2026-10-12']);base['evidence']=str(composite)
    assert anchor_sources(base,bars(['2026-10-12','2026-10-13']))=={'tencent_newfqkline'}
    assert anchor_sources(base,bars(['2026-10-09']))=={'sina_daily+sina_qfq_factor'}


def test_minute_writes_do_not_invalidate_daily_signature(tmp_path):
    import sqlite3
    from api.health_cache import market_signature
    path=str(tmp_path/'market.db');db.init_db(path)
    settings=replace(Settings(),data=replace(Settings().data,db_path=path))
    before=market_signature(settings)
    intraday=replace(settings,engine=replace(settings.engine,use_intraday_factors=True))
    full_before=market_signature(intraday)
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO minute_bars(code,date,time,close) VALUES('000001','2026-10-12','15:00',10)")
    assert market_signature(settings)==before
    assert market_signature(intraday)!=full_before


def test_cached_daily_audit_invalidates_only_new_minute_evidence(tmp_path):
    import sqlite3
    from api.health_cache import HealthCache
    path=str(tmp_path/'market.db');db.init_db(path)
    settings=replace(Settings(),data=replace(Settings().data,db_path=path),
                     paths=replace(Settings().paths,artifact_dir=str(tmp_path/'artifacts')))
    calls=[]
    cache=HealthCache(audit=lambda _: (calls.append(1) or dict(ready=True,minute_stale=False)),clock=lambda:0.)
    assert cache.get(settings)['minute_stale'] is False
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO minute_bars(code,date,time,close) VALUES('000001','2026-10-12','15:00',10)")
    result=cache.get_background(settings)
    assert result['ready'] is True
    assert result['minute_stale'] is True
    assert len(calls)==1


def test_update_skips_complete_security_and_appends_only_missing_tail(monkeypatch,tmp_path):
    from data import verified_daily as v
    path=str(tmp_path/'market.db')
    settings=replace(Settings(),data=replace(Settings().data,db_path=path,
                      start_date='2026-10-08',end_date='2026-10-12'),
                     paths=replace(Settings().paths,artifact_dir=str(tmp_path/'artifacts')))
    db.init_db(path)
    for code,days,adjust in [('000001',['2026-10-08','2026-10-09'],'qfq'),
                             ('000002',['2026-10-08','2026-10-09','2026-10-12'],'qfq'),
                             ('sh000300',['2026-10-08','2026-10-09','2026-10-12'],'none')]:
        db.publish_bar_snapshot(path,code,bars(days),source='test',adjustment=adjust,
            adjustment_version='v1',evidence='test://old',reason='fixture')
    calls=[]
    def capture(code,start,end,adjustment,root):
        calls.append((code,start,end))
        frame=bars(['2026-10-08','2026-10-09','2026-10-12'])
        frame.attrs=dict(source='test',adjustment=adjustment,adjustment_version='patch',evidence='test://patch')
        return frame
    monkeypatch.setattr(v,'capture',capture)
    result=v.update_verified_daily(settings,codes=['000001','000002'])
    assert [c[0] for c in calls]==['000001']
    assert result['updated']==['000001']
    assert result['bars_added']==1
    assert result['skipped']==['000002']
    assert not result['failed']
    assert len(db.load_training_bars(path,'000001',adjustment='qfq'))==3


def test_refresh_cooldown_survives_policy_restart_and_active_job():
    from api.market_refresh import RefreshPolicy
    now=dt.datetime.fromisoformat('2026-10-13T08:00:00+08:00')
    previous=dict(status='failed',data_only=True,finished_at=(now-dt.timedelta(minutes=2)).isoformat())
    assert not RefreshPolicy().due(now,None,job=previous)
    assert RefreshPolicy().due(now+dt.timedelta(minutes=31),None,job=previous)
    assert not RefreshPolicy().due(now,None,job=dict(status='running'))


def test_health_reports_daily_minutes_and_training_independently():
    from data.health import health_conditions
    health=dict(expected_codes=100,expected_trade_date='2026-10-12',trade_date='2026-10-12',
                trusted_trade_date='2026-10-12',trusted_latest_coverage=1.,
                trusted_coverage=.9,trusted_history_coverage=1.,benchmark_history_rows=100,
                minute_stale=True,minute_trusted_fresh_coverage=.5,minute_reason='分钟过期',
                classifications={'insufficient_recent_history':['601995']})
    value=health_conditions(health)
    assert value['daily']['ready'] is True
    assert value['minute']['ready'] is False
    assert value['training']['ready'] is False
    assert value['training']['insufficient_recent_history']==['601995']


def test_collection_completes_without_training_and_preserves_published_snapshot(monkeypatch,tmp_path):
    import threading,time
    from api import jobs
    from data import health,pipeline
    from services.result_store import ResultStore
    settings=replace(Settings(),paths=replace(Settings().paths,artifact_dir=str(tmp_path/'artifacts')))
    monkeypatch.setattr(jobs,'load_settings',lambda:settings)
    store=ResultStore(settings.paths.artifact_dir)
    now=dt.datetime.fromisoformat('2026-10-13T08:00:00+08:00')
    payload=dict(rows=[],orders=[],trade_date='2026-10-09',data_as_of=now.isoformat(),
        generated_at=now.isoformat(),versions=dict(data='old',strategy='old',parameters='old'),
        valid_until=None,allow_new_buy=False,selection=dict(funnel=[]))
    old=store.stage(payload);store.publish(old)
    entered=threading.Event();release=threading.Event();calls=[]
    def update(_):
        calls.append(1);entered.set();release.wait(3)
        return dict(requested_codes=['000001'],failed=[],failed_details={},target_trade_date='2026-10-12')
    monkeypatch.setattr(pipeline,'update_all',update)
    monkeypatch.setattr(health,'assess_data_health',lambda *a,**k:
        dict(ready=False,reason='训练历史不足',trusted_trade_date='2026-10-12'))
    registry=jobs.DailyJobRegistry();other=jobs.DailyJobRegistry()
    job=registry.start(False,data_only=True,send_notification=False)
    try:
        assert entered.wait(1)
        assert other.start(False,data_only=True)['job_id']==job['job_id']
    finally:release.set()
    for _ in range(200):
        final=registry.get(job['job_id'])
        if final['status'] in ('succeeded','failed'):break
        time.sleep(.01)
    assert final['status']=='succeeded'
    assert final['result']['result']['data_health']['ready'] is False
    assert final['result']['result']['components']['training']['ready'] is False
    assert store.read_view()['snapshot']['snapshot_id']==old
    assert len(calls)==1


def test_minute_gap_plan_detects_internal_missing_minute():
    from data.incremental import minute_missing_times
    date='2026-10-12'
    times=[f'{h:02d}:{m:02d}' for h,m in
           [(9,m) for m in range(31,60)]+[(10,m) for m in range(60)]+
           [(11,m) for m in range(31)]+[(13,m) for m in range(1,60)]+
           [(14,m) for m in range(60)]+[(15,0)]]
    frame=pd.DataFrame(dict(date=date,time=[t for t in times if t!='10:05']))
    assert minute_missing_times(frame)==['10:05']


def test_retry_route_uses_shared_collection_registry(monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app
    from api.routers import status
    calls=[]
    def start(*args,**kwargs):
        calls.append((args,kwargs));return dict(job_id='same-job',status='running',stage='fetch_daily')
    monkeypatch.setattr(status.daily_jobs,'start',start)
    client=TestClient(app)
    assert client.post('/api/status/refresh').json()['job_id']=='same-job'
    assert calls==[((False,),dict(data_only=True,send_notification=False))]


def test_live_minutes_target_current_day_with_latency_allowance():
    from data.timeliness import collection_minute_target
    assert collection_minute_target(dt.datetime.fromisoformat('2026-10-13T08:00:00+08:00'))==('2026-10-12','15:00')
    assert collection_minute_target(dt.datetime.fromisoformat('2026-10-13T10:03:00+08:00'))==('2026-10-13','09:58')


def test_adjustment_fallback_failure_keeps_old_snapshot_and_can_retry(monkeypatch,tmp_path):
    from data import verified_daily as v
    import sqlite3
    path=str(tmp_path/'market.db')
    settings=replace(Settings(),data=replace(Settings().data,db_path=path,
                      start_date='2026-10-08',end_date='2026-10-12'),
                     paths=replace(Settings().paths,artifact_dir=str(tmp_path/'artifacts')))
    db.init_db(path)
    for code,adjust in [('000001','qfq'),('sh000300','none')]:
        db.publish_bar_snapshot(path,code,bars(['2026-10-08','2026-10-09']),source='test',
            adjustment=adjust,adjustment_version='v1',evidence='test://old',reason='fixture')
    def broken_capture(code,start,end,adjustment,root):
        if code=='000001' and start=='2026-10-08':
            # Both patch and full requests include Oct 8 for this short history;
            # a changed overlap is rejected again by the authoritative fallback.
            if getattr(broken_capture,'called',False):raise ValueError('供应商完整快照无效')
            broken_capture.called=True
        frame=bars(['2026-10-08','2026-10-09','2026-10-12'],price=11 if code=='000001' else 10)
        frame.attrs=dict(source='test',adjustment=adjustment,adjustment_version='patch',evidence='test://patch')
        return frame
    monkeypatch.setattr(v,'capture',broken_capture)
    first=v.update_verified_daily(settings,codes=['000001'])
    assert first['failed']==['000001']
    with sqlite3.connect(path) as c:
        assert c.execute("select version from active_bar_snapshots where code='000001'").fetchone()[0]=='v1'
    assert len(db.load_training_bars(path,'000001',adjustment='qfq'))==2
    def good_capture(code,start,end,adjustment,root):
        frame=bars(['2026-10-08','2026-10-09','2026-10-12'])
        frame.attrs=dict(source='test',adjustment=adjustment,adjustment_version='recovered',evidence='test://recovered')
        return frame
    monkeypatch.setattr(v,'capture',good_capture)
    retry=v.update_verified_daily(settings,codes=['000001'])
    assert not retry['failed']
    assert len(db.load_training_bars(path,'000001',adjustment='qfq'))==3


def test_internal_gap_response_is_invalidated_before_retry(monkeypatch,tmp_path):
    from data import verified_daily as v
    import hashlib
    path=str(tmp_path/'market.db')
    settings=replace(Settings(),data=replace(Settings().data,db_path=path,start_date='2026-10-08',end_date='2026-10-12'),
                     paths=replace(Settings().paths,artifact_dir=str(tmp_path/'artifacts')))
    db.init_db(path)
    for code,days,adjust in [('000001',['2026-10-08','2026-10-12'],'qfq'),
                             ('sh000300',['2026-10-08','2026-10-09','2026-10-12'],'none')]:
        db.publish_bar_snapshot(path,code,bars(days),source='test',adjustment=adjust,
                               adjustment_version='v1',evidence='test://old',reason='fixture')
    calls=[]
    response=tmp_path/'patch.response';proof=tmp_path/'proof.json'
    def capture(code,start,end,adjustment,root):
        if not response.exists():
            calls.append(1)
            response.write_text(json.dumps(['2026-10-08','2026-10-12'] if len(calls)==1
                                           else ['2026-10-08','2026-10-09','2026-10-12']))
        proof.write_text(json.dumps(dict(raw_captures=[dict(raw_file=str(response),
                             sha256=hashlib.sha256(response.read_bytes()).hexdigest())])))
        frame=bars(json.loads(response.read_text()))
        frame.attrs=dict(source='test',adjustment=adjustment,adjustment_version='patch',evidence=str(proof))
        return frame
    monkeypatch.setattr(v,'capture',capture)
    assert v.update_verified_daily(settings,codes=['000001'])['failed']==['000001']
    assert not v.update_verified_daily(settings,codes=['000001'])['failed']
    assert len(calls)==2


def test_stale_conditions_and_short_benchmark_history_do_not_pass():
    from data.health import health_conditions
    h=dict(expected_codes=1,expected_trade_date='2026-10-12',trade_date='2026-10-12',
           benchmark_trusted_latest_date='2026-10-12',trusted_trade_date=None,
           trusted_latest_coverage=1.,trusted_coverage=1.,trusted_history_coverage=1.,history_coverage=1.,
           benchmark_history_rows=100,benchmark_history_start='2026-06-01',benchmark_history_cutoff='2021-01-31',
           minute_stale=False,minute_trusted_fresh_coverage=1.)
    value=health_conditions(h)
    assert value['daily']['ready'] is True
    assert value['training']['ready'] is False
    assert all(not v['ready'] for v in health_conditions(h,audit_state='passed',fresh=False).values())


def test_data_only_audit_crossing_session_boundary_cannot_certify(monkeypatch,tmp_path):
    import time
    from api import jobs
    from api import health_cache
    from data import health,pipeline,timeliness
    from services.result_store import ResultStore
    settings=replace(Settings(),paths=replace(Settings().paths,artifact_dir=str(tmp_path/'artifacts')))
    monkeypatch.setattr(jobs,'load_settings',lambda:settings)
    monkeypatch.setattr(pipeline,'update_all',lambda _:dict(requested_codes=['000001'],failed=[]))
    monkeypatch.setattr(health_cache,'market_signature',lambda _: 'fixed')
    times=iter([dt.datetime.fromisoformat('2026-10-13T16:09:59+08:00'),
                dt.datetime.fromisoformat('2026-10-13T16:10:01+08:00')])
    monkeypatch.setattr(timeliness,'exchange_now',lambda:next(times))
    monkeypatch.setattr(health,'assess_data_health',lambda *a,**k:
                         dict(ready=True,reason='',trade_date='2026-10-12',trusted_trade_date='2026-10-12'))
    registry=jobs.DailyJobRegistry();job=registry.start(False,data_only=True,send_notification=False)
    for _ in range(200):
        if registry.get(job['job_id'])['status'] in ('succeeded','failed'):break
        time.sleep(.01)
    audit=ResultStore(settings.paths.artifact_dir).read_view()['audit']
    assert not audit['ready']
    assert audit['error']['code']=='audit_version_changed'


def test_confirmed_finite_suspensions_are_not_retried_or_extended():
    from data.security_master import inactive_status
    from data.incremental import gap_plan
    for first,last,resumed in [('2025-11-20','2025-12-17','2025-12-18'),
                               ('2026-09-15','2026-09-22','2026-09-23')]:
        assert inactive_status('601995',first)['status'] == 'suspended'
        assert inactive_status('601995',last)['source']
        assert inactive_status('601995',resumed) is None
    old=bars(['2026-09-14','2026-09-23'])
    assert gap_plan(old,'601995','2026-09-14','2026-09-23') == []
    # Resume day remains required; no generated price fills a genuine missing bar.
    assert gap_plan(bars(['2026-09-14']),'601995','2026-09-14','2026-09-23')[0]['missing_dates'] == ['2026-09-23']


def test_verified_historical_suspension_does_not_become_a_permanent_exclusion():
    from data.security_master import inactive_status
    from data.incremental import gap_plan
    assert inactive_status('000063','2021-03-31')['status'] == 'suspended'
    assert inactive_status('000063','2021-03-30') is None
    assert inactive_status('000063','2021-04-01') is None
    assert inactive_status('000063','2026-10-13') is None
    assert gap_plan(bars(['2021-03-30','2021-04-01']), '000063',
                    '2021-03-30','2021-04-01') == []


def test_historical_suspension_registry_preserves_later_lifecycle_facts():
    from data.security_master import inactive_status
    assert inactive_status('601059','2025-11-20')['status'] == 'suspended'
    assert inactive_status('601059','2025-12-18') is None
    assert inactive_status('601059','2026-09-15')['status'] == 'suspended'
    assert inactive_status('601989','2024-09-03')['status'] == 'suspended'
    assert inactive_status('601989','2024-09-19') is None
    assert inactive_status('601989','2025-09-05')['status'] == 'delisted'


def test_suspension_evidence_changes_invalidate_health_signature(tmp_path, monkeypatch):
    from api.health_cache import market_signature
    from data.security_master import SECURITIES
    settings=replace(Settings(),data=replace(Settings().data,db_path=str(tmp_path/'market.db')))
    db.init_db(settings.data.db_path)
    before=market_signature(settings)
    monkeypatch.setitem(SECURITIES,'000001',{'suspension_intervals':[
        dict(start='2026-10-12',end='2026-10-12',resumed_on='2026-10-13',source='https://example.test/issuer')
    ]})
    assert market_signature(settings)!=before
