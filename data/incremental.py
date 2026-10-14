"""Plan missing sessions and join verified history only across matching anchors."""
from __future__ import annotations

import math
import datetime as dt
import json
from pathlib import Path
import pandas as pd

from data.baseline import PRICE
from data.security_master import inactive_status, security_metadata
from data.trading_calendar import expected_sessions


class AdjustmentMismatch(ValueError):
    pass


def align_patch_units(existing,patch,code):
    out=patch.copy();out.attrs=dict(patch.attrs)
    if patch.attrs.get('source')!='tencent_newfqkline' or 'evidence' not in existing:
        return out
    units=set()
    for evidence in set(existing.evidence):
        path=Path(str(evidence))
        if not path.is_file():
            return out
        units.add(json.loads(path.read_text()).get('volume_unit'))
    if units and all(unit and unit.startswith('shares') for unit in units):
        # AKShare stock_hist_tx.py normalizes this exact endpoint the same way.
        symbol=code.lower()
        number=symbol[2:] if symbol.startswith(('sh','sz','bj')) else symbol
        factor=1 if number.startswith('688') or symbol.startswith(('sh000','sz399')) else 100
        out['volume']=out.volume*factor
        out.attrs.update(volume_unit='shares',volume_multiplier=factor,
            volume_anchor_tolerance=max(patch.attrs.get('volume_quantum',0),1)*factor/2+1e-7,
            amount_anchor_tolerance=patch.attrs.get('amount_quantum',0)/2+1e-7,
            unit_contract='https://github.com/akfamily/akshare/blob/main/akshare/stock_feature/stock_hist_tx.py')
        quantums=patch.attrs.get('anchor_quantums',{})
        out.attrs['anchor_tolerances']={str(day):dict(
            volume=max(quantums.get(str(day),{}).get('volume',patch.attrs.get('volume_quantum',0)),1)*factor/2+1e-7,
            amount=quantums.get(str(day),{}).get('amount',patch.attrs.get('amount_quantum',0))/2+1e-7)
            for day in patch.date}
    return out


def anchor_sources(existing,patch):
    def origin(evidence,day,seen):
        if evidence in seen or len(seen)>16:
            return None
        seen=seen|{evidence};path=Path(evidence)
        if not path.is_file():
            return None
        proof=json.loads(path.read_text())
        if proof.get('kind')!='anchored_incremental':
            return proof.get('source')
        for part in proof.get('patches',[]):
            if day in part.get('added_dates',[]):
                return part.get('source')
        parents=proof.get('parent_evidence',[])
        return origin(parents[0],day,seen) if len(parents)==1 else None
    dates=set(existing.date.astype(str))&set(patch.date.astype(str))
    if 'evidence' not in existing:
        return {None}
    return {origin(str(row.evidence),str(row.date),set()) for row in existing.itertuples()
            if str(row.date) in dates}


def minute_missing_times(frame, through='15:00'):
    expected=[]
    for first,last in [('09:31','11:30'),('13:01','15:00')]:
        clock=dt.datetime.fromisoformat('2000-01-01T'+first)
        end=dt.datetime.fromisoformat('2000-01-01T'+last)
        while clock<=end:
            expected.append(clock.strftime('%H:%M'))
            clock+=dt.timedelta(minutes=1)
    have=set(frame.time.astype(str).str[:5]) if not frame.empty else set()
    return [minute for minute in expected if minute<=through and minute not in have]


def gap_plan(existing, code, start, end):
    listed=(security_metadata(code) or {}).get('listed_on')
    start=max(start,listed) if listed else start
    sessions=expected_sessions(start,end)
    dates={str(day) for day in existing.date} if not existing.empty else set()
    missing=[day for day in sessions if day not in dates and inactive_status(code,day) is None]
    if not missing:
        return []
    have=sorted(day for day in dates if start<=day<=end)
    windows=[]
    for day in missing:
        before=[d for d in have if d<day][-2:]
        after=[d for d in have if d>day][:2]
        first=before[0] if before else day
        last=after[-1] if after else day
        if windows and first<=windows[-1]['end']:
            windows[-1]['end']=max(last,windows[-1]['end'])
            windows[-1]['missing_dates'].append(day)
        else:
            windows.append(dict(start=first,end=last,missing_dates=[day]))
    return windows


def merge_patch(existing, patch, *, adjustment):
    old=existing.copy();new=patch.copy()
    old['date']=old.date.astype(str);new['date']=new.date.astype(str)
    if new.date.duplicated().any():
        raise ValueError('增量响应包含重复日期')
    overlap=set(old.date)&set(new.date)
    if adjustment!='none' and not overlap:
        raise AdjustmentMismatch('增量复权行情缺少已有价格锚点，需要完整重采')
    left=old.set_index('date');right=new.set_index('date')
    for day in overlap:
        if any(not math.isclose(float(left.loc[day,k]),float(right.loc[day,k]),
                                rel_tol=1e-9,abs_tol=patch.attrs.get('anchor_tolerances',{}).get(day,{}).get(k,1e-7)) for k in PRICE):
            raise AdjustmentMismatch(f'增量价格或成交数据与旧快照不一致: {day}，需要完整重采')
    # Existing rows are immutable here; incoming rows only fill absent sessions.
    return pd.concat([old,new[~new.date.isin(old.date)]],ignore_index=True).sort_values('date').reset_index(drop=True)
