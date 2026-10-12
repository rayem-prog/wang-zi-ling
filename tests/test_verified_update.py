import pytest
from data import db
from config.settings import Settings


def test_extended_amount_is_real_and_window_is_filtered():
    from data.verified_daily import parse_rows
    rows=[['2026-10-08','10','11','12','9','100',{},'0','10.5',''],
          ['2026-10-09','11','12','13','10','200',{},'0','23','']]
    frame=parse_rows('000001',rows,'2026-10-09','2026-10-09')
    assert len(frame)==1
    assert frame.iloc[0].amount==230000
    assert frame.iloc[0].volume==200
    with pytest.raises(ValueError):
        parse_rows('000001',[rows[0][:6]],'2026-10-08','2026-10-09')


def test_conflicting_annual_overlap_is_rejected():
    from data.verified_daily import merge_windows
    a=[['2026-10-08','10','11','12','9','100',{},'0','10.5','']]
    b=[['2026-10-08','10','11.5','12','9','100',{},'0','10.5','']]
    with pytest.raises(ValueError,match='冲突'):
        merge_windows('000001',[(a,'2026-10-08','2026-10-08'),(b,'2026-10-09','2026-10-09')])


def test_scheduler_retries_failed_day_and_uses_shared_registry():
    from api.market_refresh import RefreshPolicy
    import datetime as dt
    p=RefreshPolicy();now=dt.datetime(2026,10,11,16,tzinfo=dt.timezone(dt.timedelta(hours=8)))
    assert p.due(now,latest='2026-09-24')
    p.submitted(now)
    assert not p.due(now+dt.timedelta(minutes=1),latest='2026-09-24')
    assert p.due(now+dt.timedelta(minutes=31),latest='2026-09-24')
    assert not p.due(now,latest='2026-10-09')


def test_extended_provider_explicit_empty_window_and_documented_day_fallback():
    from data.verified_daily import provider_rows
    assert provider_rows({'day':[]},'qfq')==[]
    rows=[['2026-10-09','10','10','10','10','1',{},'0','1']]
    assert provider_rows({'day':rows},'qfq')==rows
    with pytest.raises(ValueError):
        provider_rows({'hfqday':rows},'qfq')


def test_incomplete_close_capture_is_refetched_and_evidence_is_preserved(monkeypatch,tmp_path):
    import json
    from data import verified_daily as v
    old=['2026-10-08','10','11','12','9','100',{},'0','10.5']
    new=['2026-10-09','11','12','13','10','200',{},'0','23']
    calls=[]
    class Response:
        def __init__(self,rows):
            self.payload={'code':0,'data':{'sz000001':{'qfqday':rows}}}
            self.content=json.dumps(self.payload).encode()
        def raise_for_status(self): pass
        def json(self): return self.payload
    class Session:
        def __init__(self): self.headers={}
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def get(self,*args,**kwargs):
            calls.append(kwargs)
            return Response([old] if len(calls)==1 else [old,new])
    monkeypatch.setattr(v.requests,'Session',Session)
    monkeypatch.setattr(v.time,'sleep',lambda *a:None)
    with pytest.raises(ValueError,match='最新交易日不完整'):
        v.capture('000001','2026-10-08','2026-10-09','qfq',tmp_path)
    frame=v.capture('000001','2026-10-08','2026-10-09','qfq',tmp_path)
    assert frame.date.max()=='2026-10-09'
    assert len(calls)==2
    assert list(tmp_path.rglob('*incomplete*.response'))
    again=v.capture('000001','2026-10-08','2026-10-09','qfq',tmp_path)
    assert len(calls)==2
    assert again.attrs==frame.attrs
