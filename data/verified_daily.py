"""真实成交额日线采集与完整复权快照发布；不沿用旧接口的估算成交额。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from decimal import Decimal
from uuid import uuid4

import pandas as pd
import requests

from data import db
from data.baseline import row_reason
from data.health import _expected_session
from data.security_master import inactive_status
from data.incremental import gap_plan, merge_patch, align_patch_units, anchor_sources, AdjustmentMismatch

URL = 'https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get'
SOURCE = 'tencent_newfqkline'
COLS = ['code','date','open','high','low','close','volume','amount']


def atomic_write(path, content):
    path=Path(path)
    temporary=path.with_name(path.name+'.tmp-'+uuid4().hex)
    try:
        temporary.write_bytes(content if isinstance(content,bytes) else content.encode())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def archive_captures(captures, label):
    for metadata in captures:
        old=Path(metadata['raw_file'])
        if old.exists():
            archived=old.with_name(old.stem+'.'+label+'-'+metadata['sha256']+'.response')
            old.replace(archived)
            metadata['archived_file']=str(archived)
        old.with_suffix('.meta.json').unlink(missing_ok=True)


def invalidate_patch(frame, missing):
    evidence=Path(frame.attrs.get('evidence',''))
    if evidence.is_file():
        proof=json.loads(evidence.read_text())
        captures=proof.get('raw_captures',[])
        archive_captures(captures,'missing-sessions')
        atomic_write(evidence.with_name(evidence.name+'.rejection.json'),
                     json.dumps(dict(evidence=str(evidence),missing_dates=missing,captures=captures),ensure_ascii=False,indent=2))


def parse_rows(code, rows, start, end):
    records=[]
    for r in rows:
        if not start <= str(r[0]) <= end:
            continue
        if len(r)<9:
            raise ValueError('供应商缺少真实成交额字段')
        record=dict(code=code,date=str(r[0]),open=float(r[1]),close=float(r[2]),
                    high=float(r[3]),low=float(r[4]),volume=float(r[5]),amount=float(r[8])*10000)
        bad=row_reason(record)
        if bad:
            raise ValueError(f'{code} {r[0]} {bad}')
        records.append(record)
    return pd.DataFrame(records,columns=COLS)


def merge_windows(code, windows):
    seen={}; frames=[]
    for rows,start,end in windows:
        # Even out-of-window overlap must agree within the same captured adjustment version.
        for r in rows:
            if len(r)<9:
                raise ValueError('供应商缺少真实成交额字段')
            value=tuple(float(r[i]) for i in (1,2,3,4,5,8))
            if r[0] in seen and seen[r[0]]!=value:
                raise ValueError(f'{code} 分页重叠冲突: {r[0]}')
            seen[r[0]]=value
        frames.append(parse_rows(code,rows,start,end))
    out=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame(columns=COLS)
    return out.drop_duplicates('date').sort_values('date').reset_index(drop=True)


def provider_rows(node, adjustment):
    key='day' if adjustment=='none' else adjustment+'day'
    if key in node:
        return node[key]
    # newfqkline 的明确契约：无复权记录时返回 day；不是旧 fqkline 的字段推断。
    # https://github.com/akfamily/akshare/blob/main/akshare/stock_feature/stock_hist_tx.py
    if 'day' in node:
        return node['day']
    raise ValueError(f'缺少请求的复权模式 {key}')


def capture(code, start, end, adjustment, root):
    from data.fetcher import _tencent_symbol, _year_windows
    symbol=code if code.startswith(('sh','sz','bj')) else _tencent_symbol(code)
    root=Path(root)/end/code/adjustment;root.mkdir(parents=True,exist_ok=True)
    windows=[];captures=[]
    with requests.Session() as session:
        session.trust_env=False
        session.headers['User-Agent']='Mozilla/5.0'
        for first,last in _year_windows(start,end):
            param=f'{symbol},day,{first},{last},640,'+('' if adjustment=='none' else adjustment)
            stem=root/f'{first}_{last}';response=stem.with_suffix('.response');meta=stem.with_suffix('.meta.json')
            cached=False
            if response.exists() and meta.exists():
                content=response.read_bytes();metadata=json.loads(meta.read_text())
                cached=(metadata.get('sha256')==hashlib.sha256(content).hexdigest()
                        and metadata.get('param')==param)
            if not cached:
                for attempt in range(3):
                    try:
                        r=session.get(URL,params={'param':param},timeout=25);r.raise_for_status()
                        content=r.content;payload=r.json()
                        if payload.get('code') not in (0,'0'):
                            raise ValueError('供应商返回错误')
                        break
                    except Exception:
                        if attempt==2: raise
                        time.sleep(2**attempt)
                metadata=dict(url=URL,param=param,sha256=hashlib.sha256(content).hexdigest(),
                              captured_at=dt.datetime.now(dt.timezone.utc).isoformat())
                atomic_write(response,content);atomic_write(meta,json.dumps(metadata,ensure_ascii=False,indent=2))
                time.sleep(.15)
            metadata=dict(metadata,raw_file=str(response.resolve()))
            captures.append(metadata)
            try:
                payload=json.loads(content)
                key='day' if adjustment=='none' else adjustment+'day'
                node=payload['data'][symbol]
                rows=provider_rows(node,adjustment)
                metadata['volume_quantum']=max((float(Decimal(10)**Decimal(str(r[5])).as_tuple().exponent) for r in rows),default=0)
                metadata['amount_quantum']=max((float(Decimal(10)**Decimal(str(r[8])).as_tuple().exponent)*10000 for r in rows if len(r)>8),default=0)
                metadata['anchor_quantums']={str(r[0]):dict(
                    volume=float(Decimal(10)**Decimal(str(r[5])).as_tuple().exponent),
                    amount=float(Decimal(10)**Decimal(str(r[8])).as_tuple().exponent)*10000)
                    for r in rows if len(r)>8}
                metadata['returned_key']=key if key in node else 'day'
                windows.append((rows,first,last))
            except (ValueError, KeyError, TypeError, IndexError, ArithmeticError):
                archive_captures(captures,'invalid')
                raise
    try:
        frame=merge_windows(code,windows)
    except (ValueError, KeyError, TypeError, IndexError):
        # Keep rejected evidence, but never turn it into a permanent reusable cache.
        archive_captures(captures,'invalid')
        raise
    if frame.empty or frame.date.max()<end:
        # 未完成当日行情不留作可复用缓存，否则首次延迟会导致当天永远无法补采。
        archive_captures(captures,'incomplete')
        raise ValueError(f'{code} 最新交易日不完整：{frame.date.max() if not frame.empty else "empty"} < {end}')
    encoded=frame.to_json(orient='records',double_precision=15)
    digest=hashlib.sha256(encoded.encode()).hexdigest()
    normalized=root/(digest+'.normalized.json');atomic_write(normalized,encoded+'\n')
    evidence_payload=json.dumps(dict(source=SOURCE,adjustment=adjustment,normalized_sha256=digest,
        normalized_file=str(normalized.resolve()),raw_captures=captures,
        amount_unit='CNY; field 8 multiplied by 10000',volume_unit='provider native; market-dependent, retained; not execution shares',
        historical_point_in_time_adjustment=False),ensure_ascii=False,indent=2)
    evidence_hash=hashlib.sha256(evidence_payload.encode()).hexdigest()
    evidence=root/(digest+'.'+evidence_hash+'.evidence.json')
    atomic_write(evidence,evidence_payload)
    frame.attrs.update(source=SOURCE,adjustment=adjustment,adjustment_version='sha256:'+digest+':'+evidence_hash,
                       evidence=str(evidence.resolve()),volume_unit='provider_native',
                       anchor_quantums={day:q for m in captures for day,q in m['anchor_quantums'].items() if start<=day<=end},
                       volume_quantum=max((m['volume_quantum'] for m in captures),default=0),
                       amount_quantum=max((m['amount_quantum'] for m in captures),default=0))
    return frame


def update_verified_daily(settings,codes=None,workers=4):
    from data.pipeline import fetch_universe
    codes=list(dict.fromkeys(codes if codes is not None else fetch_universe(settings)))
    end=settings.data.end_date or _expected_session(dt.datetime.now()).isoformat()
    excluded=[inactive_status(c,end) for c in codes if inactive_status(c,end)]
    codes=[c for c in codes if not inactive_status(c,end)]
    root=Path(settings.paths.artifact_dir)/'market-captures'
    db.init_db(settings.data.db_path)
    result=dict(requested_codes=codes,excluded_inactive_codes=excluded,updated=[],failed=[],
                failed_details={},failures=[],planned_gaps={},skipped=[],fresh_codes=[],target_trade_date=end,bars_added=0,bench_updated=False)
    def work(code):
        adjustment='none' if code==settings.data.index_symbol else settings.data.adjust
        with sqlite3.connect(settings.data.db_path) as conn:
            active=conn.execute('SELECT adjustment,version FROM active_bar_snapshots WHERE code=?',(code,)).fetchone()
            earliest=conn.execute('SELECT MIN(date) FROM bars WHERE code=?',(code,)).fetchone()[0]
        first=min(settings.data.start_date,earliest or settings.data.start_date)
        base=db.load_training_bars(settings.data.db_path,code,adjustment=adjustment,gap_policy='segments')
        if not active or base.empty:
            frame=capture(code,first,end,adjustment,root)
            return frame,active,first,[],[],len(frame)
        plan=gap_plan(base,code,first,end)
        if not plan:
            return None
        merged=base;patches=[];failures=[]
        for interval in plan:
            try:
                patch=capture(code,interval['start'],interval['end'],adjustment,root)
                patch=align_patch_units(base,patch,code)
                candidate=merge_patch(merged,patch,adjustment=adjustment)
                missing=sorted(set(interval['missing_dates'])-set(patch.date.astype(str)))
                if missing:
                    invalidate_patch(patch,missing)
                    raise ValueError('供应商仍缺少交易日: '+','.join(missing))
                patches.append(dict(patch.attrs,added_dates=sorted(set(patch.date.astype(str))-set(merged.date.astype(str)))))
                merged=candidate
            except AdjustmentMismatch as exc:
                origins=anchor_sources(base,patch)
                if origins and all(source and 'sina_daily' in source for source in origins):
                    failures.append(dict(code=code,phase='adjustment',start=interval['start'],end=interval['end'],
                        missing_dates=interval['missing_dates'],message=str(exc),retryable=True))
                    continue
                # A changed adjustment basis requires an authoritative whole snapshot.
                frame=capture(code,first,end,adjustment,root)
                return frame,active,first,plan,[],len(set(frame.date.astype(str))-set(base.date.astype(str)))
            except Exception as exc:
                failures.append(dict(code=code,phase='capture',start=interval['start'],end=interval['end'],
                    missing_dates=interval['missing_dates'],message=str(exc),retryable=True))
        if not patches:
            return None,active,first,plan,failures,0
        merged['code']=code
        normalized=merged[COLS].copy();normalized['date']=normalized.date.astype(str)
        encoded=normalized.to_json(orient='records',double_precision=15)
        digest=hashlib.sha256(encoded.encode()).hexdigest()
        directory=root/end/code/adjustment;directory.mkdir(parents=True,exist_ok=True)
        normalized_path=directory/(digest+'.incremental.normalized.json');atomic_write(normalized_path,encoded+'\n')
        proof=dict(kind='anchored_incremental',source=SOURCE,adjustment=adjustment,
                   parent_version=active[1],parent_evidence=sorted(set(base.evidence)),
                   patches=patches,intervals=plan,normalized_file=str(normalized_path.resolve()),
                   normalized_sha256=digest,historical_point_in_time_adjustment=False)
        proof['volume_unit']=patches[0].get('volume_unit','provider_native')
        proof['amount_unit']='CNY; captured provider amount, no estimation'
        proof_text=json.dumps(proof,ensure_ascii=False,sort_keys=True,indent=2)
        proof_hash=hashlib.sha256(proof_text.encode()).hexdigest()
        evidence=directory/(digest+'.'+proof_hash+'.incremental.evidence.json');atomic_write(evidence,proof_text)
        sources=sorted({part for source in base.source for part in source.split('+')}|{SOURCE})
        normalized.attrs=dict(source='+'.join(sources),adjustment=adjustment,
                              adjustment_version='sha256:'+digest+':'+proof_hash,evidence=str(evidence.resolve()))
        return normalized,active,first,plan,failures,len(normalized)-len(base)
    with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='market-fetch') as pool:
        futures={pool.submit(work,c):c for c in codes+[settings.data.index_symbol]}
        for future in as_completed(futures):
            code=futures[future]
            prepared=None
            try:
                prepared=future.result()
                added=0
                if prepared is not None:
                    frame,active,first,plan,failures,new_rows=prepared
                    result['planned_gaps'][code]=plan
                    result['failures'].extend(failures)
                    if failures:
                        result['failed'].append(code)
                        result['failed_details'][code]='; '.join(f['message'] for f in failures)
                    if frame is None:
                        continue
                    # Fetch in parallel; publish whole-history transactions on one writer.
                    publication={key:frame.attrs[key] for key in ('source','adjustment','adjustment_version','evidence')}
                    written=db.publish_bar_snapshot(settings.data.db_path,code,frame,**publication,
                        reason='按交易日缺口补采；价格锚点、旧快照及新响应存档' if plan else '真实成交额全历史重新采集；原响应及版本哈希存档',
                        expected_version=active[1] if active else None,expected_adjustment=active[0] if active else None)
                    added=new_rows if written else 0
                    db.mark_fresh(settings.data.db_path,code)
                    db.record_fetch(settings.data.db_path,code,first,end,SOURCE,'ok',added)
                result['bars_added']+=added
                if code==settings.data.index_symbol: result['bench_updated']=bool(added)
                elif added: result['updated'].append(code)
                else: result['skipped'].append(code)
            except Exception as exc:
                if code not in result['failed']:
                    result['failed'].append(code)
                result['failed_details'][code]=str(exc)
                result['failures'].append(dict(code=code,phase='publish' if prepared is not None else 'capture',
                    message=str(exc),retryable=True))
                db.record_fetch(settings.data.db_path,code,settings.data.start_date,end,SOURCE,'error',0,str(exc))
    result['fresh_codes']=[c for c in codes if (db.get_latest_bar_date(settings.data.db_path,c) or '')>=end]
    result['adjustment']=settings.data.adjust
    result['database']=str(Path(settings.data.db_path).resolve())
    path=root/('latest-update-'+settings.data.adjust+'.json');path.parent.mkdir(parents=True,exist_ok=True)
    atomic_write(path,json.dumps(result,ensure_ascii=False,indent=2))
    return result
