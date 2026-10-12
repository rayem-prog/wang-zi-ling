import datetime as dt
import json
import sqlite3
from dataclasses import replace
from importlib import import_module

import pandas as pd
import pytest

from config.settings import Settings
from data import db


def bars(dates=("2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24")):
    return pd.DataFrame([dict(date=d, open=10., high=12., low=9., close=11., volume=100., amount=1100.) for d in dates])


def require(module, name):
    assert hasattr(module, name), f"Missing required behavior: {name}"
    return getattr(module, name)


def publish(path, frame, version="v1", code="000001"):
    return require(db, "publish_bar_snapshot")(path, code, frame, source="tencent", adjustment="qfq",
        adjustment_version=version, evidence="test://captured-provider-response", reason="核对供应商快照",
        expected_version="v1" if version!="v1" else None)


def test_unknown_provenance_is_quarantined_but_original_values_survive(tmp_path):
    path = str(tmp_path / "market.db")
    db.init_db(path)
    db.save_bars(path, "000001", bars(), source="legacy_unknown")
    load = require(db, "load_training_bars")
    assert load(path, "000001").empty
    assert len(db.load_bars(path, "000001")) == 4
    with sqlite3.connect(path) as con:
        status = con.execute("SELECT verification_status,quarantine_reason FROM bars LIMIT 1").fetchone()
    assert status[0] == "quarantined"
    assert status[1]


def test_named_provider_without_adjustment_evidence_is_not_trusted(tmp_path):
    path = str(tmp_path / "market.db")
    db.init_db(path)
    db.save_bars(path, "000001", bars(), source="tencent")
    assert require(db, "load_training_bars")(path, "000001").empty


def test_closed_day_rejected_from_snapshot_without_mutating_storage(tmp_path):
    path = str(tmp_path / "market.db")
    db.init_db(path)
    with pytest.raises(ValueError, match="交易日|休市"):
        publish(path, bars(("2026-09-24", "2026-09-25")))
    assert db.load_bars(path, "000001").empty


def test_snapshot_version_switch_keeps_complete_before_after_history(tmp_path):
    path = str(tmp_path / "market.db")
    db.init_db(path)
    publish(path, bars())
    changed = bars()
    changed["close"] = 10.5
    publish(path, changed, "v2")
    loaded = require(db, "load_training_bars")(path, "000001")
    assert loaded["close"].tolist() == [10.5]*4
    assert loaded["adjustment_version"].unique().tolist() == ["v2"]
    with sqlite3.connect(path) as con:
        old = con.execute("SELECT before_json,after_json FROM bar_revisions WHERE action='snapshot' AND before_json IS NOT NULL ORDER BY id").fetchall()
    assert len(old) == 4
    assert json.loads(old[0][0])["close"] == 11.
    assert json.loads(old[0][1])["close"] == 10.5
    assert json.loads(old[0][0])["adjustment_version"] == "v1"


def test_partial_replacement_and_reused_version_are_rejected(tmp_path):
    path = str(tmp_path / "market.db")
    db.init_db(path)
    publish(path, bars())
    with pytest.raises(ValueError, match="完整|缺少"):
        publish(path, bars().iloc[1:], "v2")
    changed = bars()
    changed["close"] = 10.
    with pytest.raises(ValueError, match="版本"):
        publish(path, changed, "v1")
    assert db.load_training_bars(path, "000001")["close"].tolist() == [11.]*4


def test_conflicting_incremental_write_is_logged_and_does_not_replace_prices(tmp_path):
    path = str(tmp_path / "market.db")
    db.init_db(path)
    publish(path, bars())
    new = bars()
    new["close"] = 10.
    assert db.save_bars(path, "000001", new, source="tencent") == 0
    with sqlite3.connect(path) as con:
        record = con.execute("SELECT before_json,after_json FROM bar_revisions WHERE action='conflict' LIMIT 1").fetchone()
    assert json.loads(record[0])["close"] == 11.
    assert json.loads(record[1])["close"] == 10.
    assert db.load_training_bars(path, "000001")["close"].iloc[0] == 11.


def test_snapshot_with_unexplained_internal_gap_is_not_trainable(tmp_path):
    path = str(tmp_path / "market.db")
    db.init_db(path)
    publish(path, bars(("2026-09-21", "2026-09-23", "2026-09-24")))
    assert db.load_training_bars(path, "000001").empty


def test_audit_splits_missing_sessions_by_evidence(tmp_path):
    path = str(tmp_path / "market.db")
    db.init_db(path)
    db.save_bars(path, "000001", bars(("2026-09-18", "2026-09-24")), source="legacy_unknown")
    db.record_fetch(path, "000001", "2026-09-21", "2026-09-21", "tencent", "error", 0, "network down")
    assert hasattr(db, "audit_baseline")
    report = db.audit_baseline(path)
    gaps = [g for g in report["internal_gaps"] if g["code"] == "000001"]
    assert [(g["start"],g["end"],g["classification"],g["sessions"]) for g in gaps] == [
        ("2026-09-21","2026-09-21","source_error",1),
        ("2026-09-22","2026-09-23","unexplained_gap",2)]
    assert gaps[0]["evidence"]
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM data_gap_segments").fetchone()[0] == 2
    db.audit_baseline(path)
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM baseline_audits").fetchone()[0] == 2


def test_health_blocks_unknown_prices_even_when_raw_date_coverage_is_complete(tmp_path):
    from data.health import assess_data_health
    path = str(tmp_path / "market.db")
    db.init_db(path)
    for code in ("000001","sh000300"):
        db.save_bars(path, code, bars(), source="tencent")
    settings = replace(Settings(),data=replace(Settings().data,db_path=path))
    result = assess_data_health(settings,now=dt.datetime(2026,9,24,17),expected_codes=["000001"])
    assert result["ready"] is False
    assert result.get("trusted_daily_rows") == 0
    assert result.get("quarantined_daily_rows") == 8


@pytest.mark.parametrize("now,date,time,stale", [
    (dt.datetime(2026,9,28,10,10),"2026-09-28","09:40",True),
    (dt.datetime(2026,9,28,10,10),"2026-09-28","10:08",False),
    (dt.datetime(2026,9,28,12,15),"2026-09-28","11:30",False),
    (dt.datetime(2026,9,28,12,15),"2026-09-28","10:30",True),
    (dt.datetime(2026,9,28,17),"2026-09-28","10:30",True),
    (dt.datetime(2026,9,27,12),"2026-09-24","15:00",False),
    (dt.datetime(2026,9,28,10),"2026-09-28","14:00",True),
    (dt.datetime(2026,9,28,10),"2026-09-29","09:30",True),
    (dt.datetime(2026,10,10,12),"2026-09-28","09:53",True),
])
def test_minute_freshness_uses_latest_minute_not_just_date(now,date,time,stale):
    from api.routers.kline import _response
    frame = bars((date,))
    frame["time"] = time
    # Public response uses the same clock-aware helper as the health check.
    from data import health
    assess = require(health,"minute_cache_state")
    assert assess(date,time,now=now)["stale"] is stale


def test_training_labels_do_not_bridge_a_missing_benchmark_session():
    from model.dataset import add_future_excess
    dates=[dt.date(2026,9,d) for d in (14,15,16,17,18,21,22)]
    bench=pd.DataFrame({"date":dates,"close":[10.]*7})
    stock=pd.DataFrame({"code":"000001","date":[dates[i] for i in (0,2,3,4,5,6)],"close":[10.,11.,12.,13.,14.,15.]})
    result=add_future_excess(stock,bench,horizon=5)
    assert pd.isna(result.iloc[0]["label"])


def test_strict_training_rejects_frames_without_verification_evidence():
    from model.dataset import build_training_data
    frame=bars()
    frame["code"]="000001"
    with pytest.raises(ValueError,match="验证|可信"):
        build_training_data(frame,frame[["date","close"]],require_verified=True)


def test_kline_marks_same_day_old_minute_as_stale():
    from api.routers.kline import _response
    frame=bars(("2026-09-28",))
    frame['time']='09:40'
    result=_response('000001',frame,minute=True,now=dt.datetime(2026,9,28,10,10))
    assert result['stale'] is True
    assert result['cache_status']=='stale'
    assert result['verification_status']=='quarantined'


def test_direct_price_tampering_invalidates_published_snapshot(tmp_path):
    path=str(tmp_path/'market.db')
    db.init_db(path)
    publish(path,bars())
    with sqlite3.connect(path) as con:
        con.execute("UPDATE bars SET close=10 WHERE code='000001' AND date='2026-09-21'")
    assert db.load_training_bars(path,'000001').empty


def test_correction_requires_expected_before_and_keeps_atomic_history(tmp_path):
    path=str(tmp_path/'market.db')
    db.init_db(path)
    db.save_bars(path,'000001',bars(),source='legacy_unknown')
    correct=require(db,'correct_bar_record')
    before={'close':11.}
    correct(path,'000001','2026-09-21',changes={'close':10.5},expected_before=before,
            reason='核对原始档案',evidence='test://original-file')
    with pytest.raises(ValueError,match='变化|冲突'):
        correct(path,'000001','2026-09-21',changes={'close':10.},expected_before=before,
                reason='过期纠错',evidence='test://original-file')
    with sqlite3.connect(path) as con:
        records=con.execute("SELECT before_json,after_json FROM bar_revisions WHERE action='correction'").fetchall()
        assert len(records)==1
        assert json.loads(records[0][0])['close']==11.
        assert json.loads(records[0][1])['close']==10.5
    assert db.load_training_bars(path,'000001').empty


def test_archives_preserve_minute_verification_metadata(tmp_path):
    from data import archive
    path=str(tmp_path/'market.db')
    db.init_db(path)
    frame=bars(('2026-07-01','2026-07-02','2026-08-11'))
    frame['time']='15:00'
    frame.attrs.update(adjustment='none',adjustment_version='raw-v1',evidence='test://response')
    db.save_minute_bars(path,'000001',frame,source='tencent_mkline')
    archive.archive_cold_minute_bars(path,str(tmp_path/'archive'),keep_trading_days=1)
    parquet=pd.read_parquet(tmp_path/'archive'/'minute_2026-07.parquet.zst')
    assert parquet['verification_status'].tolist()==['verified','verified']
    assert parquet['evidence'].tolist()==['test://response']*2


def test_strict_training_rejects_mixed_modes_and_unknown_evidence():
    from model.dataset import build_training_data
    frame=bars();frame['code']='000001'
    frame['verification_status']='verified';frame['adjustment']='qfq'
    frame['adjustment_version']='v1';frame['evidence']='test://response'
    frame.loc[1,'adjustment']='hfq'
    with pytest.raises(ValueError,match='复权'):
        build_training_data(frame,frame,require_verified=True)


def test_captured_minute_provider_output_has_resolvable_evidence(monkeypatch,tmp_path):
    from data import fetcher
    payload={'code':0,'data':{'sh600000':{'m1':[['202609280931','9.02','9.05','9.06','9.01','200',{},'0.66']]}}}
    class Response:
        text='m1_today='+json.dumps(payload)
        def raise_for_status(self): pass
    monkeypatch.setattr(fetcher._SESSION,'get',lambda *a,**kw:Response())
    frame=fetcher.fetch_minute_bars('600000')
    path=str(tmp_path/'market.db');db.init_db(path)
    db.save_minute_bars(path,'600000',frame,source=frame.attrs['source'])
    with sqlite3.connect(path) as con:
        row=con.execute('SELECT verification_status,adjustment,evidence FROM minute_bars').fetchone()
        assert row[0:2]==('verified','none')
        evidence=con.execute('SELECT payload_json FROM data_source_batches WHERE evidence=?',(row[2],)).fetchone()
    assert evidence
    assert '2026-09-28' in evidence[0]


def test_training_minute_reads_exclude_unverified_and_closed_rows(tmp_path):
    path=str(tmp_path/'market.db');db.init_db(path)
    frame=bars(('2026-09-24','2026-09-25'));frame['time']='15:00'
    frame.attrs.update(adjustment='none',adjustment_version='raw-v1',evidence='test://response')
    db.save_minute_bars(path,'000001',frame,source='tencent_mkline')
    read=require(db,'load_training_minute_bars')
    assert read(path,'000001')['date'].tolist()==['2026-09-24']


def test_missing_benchmark_day_cannot_compress_label_horizon():
    from model.dataset import add_future_excess
    dates=[dt.date(2026,9,d) for d in (14,15,16,17,18,21,22)]
    stock=pd.DataFrame({'code':'000001','date':dates,'close':[10.,11.,12.,13.,14.,15.,16.]})
    bench=pd.DataFrame({'date':[dates[i] for i in (0,2,3,4,5,6)],'close':[10.]*6})
    out=add_future_excess(stock,bench)
    assert pd.isna(out.iloc[0]['label'])


def test_model_prediction_uses_only_numeric_factor_schema(tmp_path):
    from scripts.daily_update import _score_a
    from features.alpha import FACTOR_COLUMNS
    from model import trainer
    from sklearn.linear_model import LogisticRegression
    import numpy as np
    artifact=tmp_path/'artifacts';artifact.mkdir()
    model=LogisticRegression().fit(np.array([[0.]*18,[1.]*18]),[0,1])
    trainer.save_model(model,str(artifact/'model.joblib'))
    settings=replace(Settings(),paths=replace(Settings().paths,artifact_dir=str(artifact)))
    frame=pd.DataFrame([{**{col:0.5 for col in FACTOR_COLUMNS},'code':'000001','date':dt.date(2026,9,24),
                         'verification_status':'verified','adjustment':'qfq','adjustment_version':'v1','evidence':'test://response'}])
    result=_score_a(settings,frame,frame,['000001'])
    assert len(result)==1
    assert 49 < result['score_a'].iloc[0] < 51


def test_intraday_gate_does_not_combine_trusted_old_minute_with_unverified_latest(tmp_path):
    from data.health import assess_data_health
    from data.trading_calendar import expected_sessions
    path=str(tmp_path/'market.db');db.init_db(path)
    history=bars(expected_sessions('2026-01-01','2026-09-24'))
    publish(path,history)
    db.publish_bar_snapshot(path,'sh000300',history,source='test',adjustment='none',adjustment_version='v1',evidence='test://index',reason='fixture')
    frame=bars(('2026-09-24',));frame['time']='09:31'
    frame.attrs.update(adjustment='none',adjustment_version='raw-v1',evidence='test://minute')
    db.save_minute_bars(path,'000001',frame,source='tencent_mkline')
    late=bars(('2026-09-24',));late['time']='15:00'
    db.save_minute_bars(path,'000001',late,source='legacy_unknown')
    settings=replace(Settings(),data=replace(Settings().data,db_path=path,start_date='2026-01-01'),engine=replace(Settings().engine,use_intraday_factors=True))
    result=assess_data_health(settings,now=dt.datetime(2026,9,24,17),expected_codes=['000001'])
    assert result['ready'] is False
    assert result.get('minute_trusted_fresh_coverage')==0.


def test_qfq_minute_refresh_replaces_complete_day_version_with_audit(tmp_path):
    from data.fetcher import _capture_provider_output
    path=str(tmp_path/'market.db');db.init_db(path)
    for times in (['09:31'],['09:31','09:32']):
        frame=bars(('2026-09-24',)*len(times));frame['time']=times;frame.attrs['source']='eastmoney'
        frame=_capture_provider_output(frame,adjustment='qfq',request={'code':'000001'})
        db.save_minute_bars(path,'000001',frame,source='eastmoney')
    loaded=db.load_training_minute_bars(path,'000001')
    assert loaded['time'].tolist()==['09:31','09:32']
    assert loaded['adjustment_version'].nunique()==1
    with sqlite3.connect(path) as con:
        row=con.execute("SELECT before_json,after_json FROM bar_revisions WHERE kind='minute' AND action='snapshot' AND before_json IS NOT NULL").fetchone()
    assert row and json.loads(row[0])['adjustment_version']!=json.loads(row[1])['adjustment_version']


def test_snapshot_replacement_requires_expected_active_identity(tmp_path):
    path=str(tmp_path/'market.db');db.init_db(path);publish(path,bars())
    with pytest.raises(ValueError,match='旧版本|预期'):
        db.publish_bar_snapshot(path,'000001',bars(),source='tencent',adjustment='qfq',adjustment_version='v2',evidence='test://new',reason='correction')


def test_adjusted_minute_version_cannot_be_reused_for_different_prices(tmp_path):
    path=str(tmp_path/'market.db');db.init_db(path)
    frame=bars(('2026-09-24',));frame['time']='09:31'
    frame.attrs.update(adjustment='qfq',adjustment_version='v1',evidence='test://minute')
    db.save_minute_bars(path,'000001',frame,source='eastmoney')
    changed=frame.copy();changed['close']=10.
    with pytest.raises(ValueError,match='版本'):
        db.save_minute_bars(path,'000001',changed,source='eastmoney')
    assert db.load_training_minute_bars(path,'000001')['close'].tolist()==[11.]


def test_adjusted_minute_training_cannot_mix_versions_across_dates(tmp_path):
    path=str(tmp_path/'market.db');db.init_db(path)
    for date,version in [('2026-09-23','v1'),('2026-09-24','v2')]:
        frame=bars((date,));frame['time']='15:00'
        frame.attrs.update(adjustment='qfq',adjustment_version=version,evidence='test://minute')
        db.save_minute_bars(path,'000001',frame,source='eastmoney')
    assert db.load_training_minute_bars(path,'000001').empty
    assert len(db.load_training_minute_bars(path,'000001',date='2026-09-24'))==1


def test_live_training_minute_read_returns_empty_for_older_day(tmp_path):
    path=str(tmp_path/'market.db');db.init_db(path)
    frame=bars(('2026-09-24',));frame['time']='15:00'
    frame.attrs.update(adjustment='none',adjustment_version='raw-v1',evidence='test://minute')
    db.save_minute_bars(path,'000001',frame,source='tencent_mkline')
    assert db.load_training_minute_bars(path,'000001',live=True,now=dt.datetime(2026,9,28,10)).empty


def test_tencent_cannot_label_raw_day_as_adjusted(monkeypatch):
    from data import fetcher
    class Response:
        def json(self):
            return {'data':{'sz000001':{'day':[['2026-09-24',10,11,12,9,100]]}}}
    monkeypatch.setattr(fetcher._SESSION,'get',lambda *a,**kw:Response())
    with pytest.raises(ValueError,match='复权|qfq'):
        fetcher._fetch_kline_tencent('sz000001','2026-09-24','2026-09-24',adjust='qfq')
