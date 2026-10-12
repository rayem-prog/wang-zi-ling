"""真实成交额日线采集与完整复权快照发布；不沿用旧接口的估算成交额。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

import pandas as pd
import requests

from data import db
from data.baseline import row_reason
from data.health import _expected_session
from data.security_master import inactive_status

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
            payload=json.loads(content)
            key='day' if adjustment=='none' else adjustment+'day'
            node=payload['data'][symbol]
            rows=provider_rows(node,adjustment)
            metadata=dict(metadata,returned_key=key if key in node else 'day',raw_file=str(response.resolve()))
            windows.append((rows,first,last));captures.append(metadata)
    frame=merge_windows(code,windows)
    if frame.empty or frame.date.max()<end:
        # 未完成当日行情不留作可复用缓存，否则首次延迟会导致当天永远无法补采。
        for metadata in captures:
            old=Path(metadata['raw_file'])
            old.replace(old.with_name(old.stem+'.incomplete-'+metadata['sha256']+'.response'))
            old.with_suffix('.meta.json').unlink(missing_ok=True)
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
                       evidence=str(evidence.resolve()))
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
                failed_details={},skipped=[],fresh_codes=[],target_trade_date=end,bars_added=0,bench_updated=False)
    def work(code):
        adjustment='none' if code==settings.data.index_symbol else settings.data.adjust
        with sqlite3.connect(settings.data.db_path) as conn:
            active=conn.execute('SELECT adjustment,version FROM active_bar_snapshots WHERE code=?',(code,)).fetchone()
            earliest=conn.execute('SELECT MIN(date) FROM bars WHERE code=?',(code,)).fetchone()[0]
        if active and not db.load_training_bars(settings.data.db_path,code,adjustment=adjustment).empty:
            if db.get_latest_bar_date(settings.data.db_path,code)>=end:
                return code,0
        first=min(settings.data.start_date,earliest or settings.data.start_date)
        frame=capture(code,first,end,adjustment,root)
        added=db.publish_bar_snapshot(settings.data.db_path,code,frame,**frame.attrs,
            reason='真实成交额全历史重新采集；原响应及版本哈希存档',
            expected_version=active[1] if active else None,expected_adjustment=active[0] if active else None)
        db.mark_fresh(settings.data.db_path,code)
        db.record_fetch(settings.data.db_path,code,first,end,SOURCE,'ok',added)
        return code,added
    with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='market-fetch') as pool:
        futures={pool.submit(work,c):c for c in codes+[settings.data.index_symbol]}
        for future in as_completed(futures):
            code=futures[future]
            try:
                _,added=future.result();result['bars_added']+=added
                if code==settings.data.index_symbol: result['bench_updated']=bool(added)
                elif added: result['updated'].append(code)
                else: result['skipped'].append(code)
            except Exception as exc:
                result['failed'].append(code);result['failed_details'][code]=str(exc)
                db.record_fetch(settings.data.db_path,code,settings.data.start_date,end,SOURCE,'error',0,str(exc))
    result['fresh_codes']=[c for c in codes if (db.get_latest_bar_date(settings.data.db_path,c) or '')>=end]
    result['adjustment']=settings.data.adjust
    result['database']=str(Path(settings.data.db_path).resolve())
    path=root/('latest-update-'+settings.data.adjust+'.json');path.parent.mkdir(parents=True,exist_ok=True)
    atomic_write(path,json.dumps(result,ensure_ascii=False,indent=2))
    return result
