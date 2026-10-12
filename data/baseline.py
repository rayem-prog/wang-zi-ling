"""保守验证、快照版本、纠错审计和可信训练读取。原始行情仍可单独读取。"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import sqlite3
from pathlib import Path

import pandas as pd

from data.gaps import classify_internal_gaps
from data.timeliness import exchange_now
from data.trading_calendar import calendar_verified, closure_kind

META = {'verification_status': "TEXT NOT NULL DEFAULT 'quarantined'", 'quarantine_reason': "TEXT NOT NULL DEFAULT 'missing_provenance_or_adjustment'",
        'adjustment': "TEXT NOT NULL DEFAULT 'unknown'", 'adjustment_version': "TEXT NOT NULL DEFAULT 'unknown'", 'evidence': "TEXT NOT NULL DEFAULT ''"}
PRICE = ['open','high','low','close','volume','amount']
UNKNOWN = {'','unknown','legacy_unknown','simulation','demo','synthetic','tencent_price_line'}


def stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='microseconds')


def encode(row: dict) -> str:
    return json.dumps(row,sort_keys=True,ensure_ascii=False,allow_nan=False)


def row_reason(row: dict, *, minute: bool = False) -> str:
    try:
        day = dt.date.fromisoformat(str(row['date']))
        if not calendar_verified(day):
            return 'calendar_unverified'
        if closure_kind(day):
            return 'closed_session'
        if day > exchange_now().date():
            return 'future_session'
        values = [float(row[k]) for k in PRICE[:5]]
        if not all(math.isfinite(v) for v in values):
            return 'invalid_ohlcv'
        o,h,l,c,v = values
        if min(o,h,l,c) <= 0 or l > min(o,c) or h < max(o,c) or l > h or v < 0:
            return 'invalid_ohlcv'
        if row.get('amount') is not None and (not math.isfinite(float(row['amount'])) or float(row['amount']) < 0):
            return 'invalid_amount'
        if minute:
            clock = dt.time.fromisoformat(str(row['time']))
            if not (dt.time(9,30) <= clock <= dt.time(11,30) or dt.time(13) <= clock <= dt.time(15)):
                return 'invalid_minute_time'
            if dt.datetime.combine(day,clock,exchange_now().tzinfo) > exchange_now():
                return 'future_minute'
    except (ValueError,TypeError,KeyError):
        return 'invalid_date_or_values'
    return ''


def snapshot_hash(rows) -> str:
    names = ['code','date']+PRICE+['source','adjustment','adjustment_version','evidence']
    payload = [{k:r[k] for k in names} for r in rows]
    return hashlib.sha256(encode(payload).encode()).hexdigest()


def migrate(conn: sqlite3.Connection) -> None:
    for table in ('bars','minute_bars'):
        columns = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        added = False
        for name,definition in META.items():
            if name not in columns:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
                added = True
        if added:
            # No source or adjustment is inferred from legacy prices.
            for date, in conn.execute(f'SELECT DISTINCT date FROM {table}').fetchall():
                try:
                    day=dt.date.fromisoformat(date)
                    reason = ('calendar_unverified' if not calendar_verified(day) else
                              'closed_session' if closure_kind(day) else
                              'future_session' if day > exchange_now().date() else '')
                except (ValueError,TypeError):
                    reason = 'invalid_date_or_values'
                if reason:
                    conn.execute(f'UPDATE {table} SET quarantine_reason=? WHERE date=?',(reason,date))
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS bar_revisions (
            id INTEGER PRIMARY KEY, code TEXT NOT NULL, date TEXT NOT NULL, time TEXT,
            kind TEXT NOT NULL DEFAULT 'daily', action TEXT NOT NULL, before_json TEXT, after_json TEXT NOT NULL,
            reason TEXT NOT NULL, evidence TEXT NOT NULL, recorded_at TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS revisions_no_update BEFORE UPDATE ON bar_revisions BEGIN SELECT RAISE(ABORT,'Audit is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS revisions_no_delete BEFORE DELETE ON bar_revisions BEGIN SELECT RAISE(ABORT,'Audit is append-only'); END;
        CREATE TABLE IF NOT EXISTS data_source_batches (
            evidence TEXT PRIMARY KEY, source TEXT NOT NULL, captured_at TEXT NOT NULL, payload_json TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS batches_no_update BEFORE UPDATE ON data_source_batches BEGIN SELECT RAISE(ABORT,'Evidence is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS batches_no_delete BEFORE DELETE ON data_source_batches BEGIN SELECT RAISE(ABORT,'Evidence is append-only'); END;
        CREATE TABLE IF NOT EXISTS bar_snapshots (
            code TEXT NOT NULL, adjustment TEXT NOT NULL, version TEXT NOT NULL,
            content_hash TEXT NOT NULL, evidence TEXT NOT NULL, published_at TEXT NOT NULL,
            PRIMARY KEY(code,adjustment,version));
        CREATE TABLE IF NOT EXISTS minute_snapshots (
            code TEXT NOT NULL, date TEXT NOT NULL, adjustment TEXT NOT NULL, version TEXT NOT NULL,
            content_hash TEXT NOT NULL, evidence TEXT NOT NULL, published_at TEXT NOT NULL,
            PRIMARY KEY(code,date,adjustment,version));
        CREATE TABLE IF NOT EXISTS active_minute_snapshots (
            code TEXT NOT NULL, date TEXT NOT NULL, adjustment TEXT NOT NULL, version TEXT NOT NULL, PRIMARY KEY(code,date));
        CREATE TRIGGER IF NOT EXISTS minute_snapshots_no_update BEFORE UPDATE ON minute_snapshots BEGIN SELECT RAISE(ABORT,'Snapshot is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS minute_snapshots_no_delete BEFORE DELETE ON minute_snapshots BEGIN SELECT RAISE(ABORT,'Snapshot is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS bar_snapshots_no_update BEFORE UPDATE ON bar_snapshots BEGIN SELECT RAISE(ABORT,'Snapshot is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS bar_snapshots_no_delete BEFORE DELETE ON bar_snapshots BEGIN SELECT RAISE(ABORT,'Snapshot is append-only'); END;
        CREATE TABLE IF NOT EXISTS active_bar_snapshots (
            code TEXT PRIMARY KEY, adjustment TEXT NOT NULL, version TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS baseline_audits (
            id INTEGER PRIMARY KEY, recorded_at TEXT NOT NULL, report_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS data_gap_segments (
            id INTEGER PRIMARY KEY, audit_id INTEGER NOT NULL REFERENCES baseline_audits(id),
            code TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL, sessions INTEGER NOT NULL,
            classification TEXT NOT NULL, evidence TEXT NOT NULL, dates_json TEXT NOT NULL);
        CREATE VIEW IF NOT EXISTS trusted_bars AS SELECT b.* FROM bars b JOIN active_bar_snapshots a
            ON b.code=a.code AND b.adjustment=a.adjustment AND b.adjustment_version=a.version
            WHERE b.verification_status='verified' AND b.evidence<>'';
        CREATE VIEW IF NOT EXISTS trusted_minute_bars AS SELECT * FROM minute_bars
            WHERE verification_status='verified' AND evidence<>'' AND adjustment<>'unknown' AND adjustment_version<>'unknown';
    ''')


def revision(conn, before, after, action, reason, evidence, kind='daily'):
    conn.execute('INSERT INTO bar_revisions(code,date,time,kind,action,before_json,after_json,reason,evidence,recorded_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                 (after['code'],after['date'],after.get('time'),kind,action,encode(before) if before else None,encode(after),reason,evidence,stamp()))


def save_rows(conn, table: str, rows: list[dict], *, adjustment='unknown', adjustment_version='unknown', evidence='') -> int:
    added=0
    minute = table=='minute_bars'
    if minute and adjustment in {'qfq','hfq'} and adjustment_version not in {'','unknown'} and evidence.strip() and all(all(s.strip().lower() not in UNKNOWN for s in r['source'].split('+')) for r in rows):
        return save_adjusted_minutes(conn,rows,adjustment,adjustment_version,evidence)
    keys = ['code','date','time'] if minute else ['code','date']
    for row in rows:
        reason = row_reason(row,minute=minute)
        metadata_known = (all(s.strip().lower() not in UNKNOWN for s in row['source'].split('+')) and
                          adjustment in {'qfq','hfq','none'} and adjustment_version not in {'','unknown'} and bool(evidence.strip()))
        reason = reason or ('' if metadata_known else 'missing_provenance_or_adjustment')
        if not minute and not reason:
            reason = 'snapshot_not_published'
        row.update(verification_status='verified' if not reason else 'quarantined',quarantine_reason=reason,
                   adjustment=adjustment,adjustment_version=adjustment_version,evidence=evidence)
        old = conn.execute(f"SELECT * FROM {table} WHERE "+' AND '.join(k+'=?' for k in keys),[row[k] for k in keys]).fetchone()
        if old:
            before=dict(old)
            if any(before[k]!=row[k] for k in PRICE):
                revision(conn,before,row,'conflict','增量价格冲突，保留原值待核实',evidence,'minute' if minute else 'daily')
            continue
        names=list(row)
        conn.execute(f"INSERT INTO {table}({','.join(names)}) VALUES({','.join('?' for _ in names)})",[row[k] for k in names])
        added+=1
    return added


def minute_snapshot_hash(rows):
    names=['code','date','time']+PRICE+['source','adjustment','adjustment_version','evidence']
    return hashlib.sha256(encode([{k:r[k] for k in names} for r in rows]).encode()).hexdigest()


def save_adjusted_minutes(conn, rows, adjustment, version, evidence):
    """完整日内批次覆盖旧版本；覆盖不全时保留旧值并隔离增量，不能拼接复权快照。"""
    from collections import defaultdict
    groups=defaultdict(list)
    for row in rows:
        groups[(row['code'],row['date'])].append(row)
    added=0
    for (code,date),incoming in groups.items():
        existing={r['time']:dict(r) for r in conn.execute('SELECT * FROM minute_bars WHERE code=? AND date=?',(code,date))}
        times={r['time'] for r in incoming}
        if len(times)!=len(incoming):
            raise ValueError('复权分钟快照包含重复时间')
        required={time for time,r in existing.items() if not row_reason(r,minute=True)}
        complete=required<=times and not any(row_reason(r,minute=True) for r in incoming)
        if complete:
            for row in incoming:
                row.update(adjustment=adjustment,adjustment_version=version,evidence=evidence)
            content=minute_snapshot_hash(sorted(incoming,key=lambda r:r['time']))
            used=conn.execute('SELECT content_hash FROM minute_snapshots WHERE code=? AND date=? AND adjustment=? AND version=?',
                              (code,date,adjustment,version)).fetchone()
            active=conn.execute('SELECT adjustment,version FROM active_minute_snapshots WHERE code=? AND date=?',(code,date)).fetchone()
            if used:
                if used[0]!=content:
                    raise ValueError('分钟复权版本不能重用于不同价格内容')
                if not active or (active[0],active[1])!=(adjustment,version):
                    raise ValueError('分钟旧版本不能重新激活')
                continue
            conn.execute('INSERT INTO minute_snapshots VALUES(?,?,?,?,?,?,?)',(code,date,adjustment,version,content,evidence,stamp()))
            conn.execute('INSERT OR REPLACE INTO active_minute_snapshots VALUES(?,?,?,?)',(code,date,adjustment,version))
        for row in incoming:
            reason=row_reason(row,minute=True) or ('' if complete else 'incomplete_adjusted_minute_snapshot')
            row.update(verification_status='verified' if not reason else 'quarantined',quarantine_reason=reason,
                       adjustment=adjustment,adjustment_version=version,evidence=evidence)
            before=existing.get(row['time'])
            if not complete and before:
                if any(before[k]!=row[k] for k in PRICE):
                    revision(conn,before,row,'conflict','分钟复权批次覆盖不全，保留旧版本',evidence,'minute')
                continue
            if before and all(before[k]==row[k] for k in PRICE+['source','verification_status','adjustment','adjustment_version','evidence']):
                continue
            revision(conn,before,row,'snapshot' if complete else 'quarantine','完整分钟复权版本发布' if complete else '分钟批次覆盖不全',evidence,'minute')
            names=list(row)
            conn.execute(f"INSERT OR REPLACE INTO minute_bars({','.join(names)}) VALUES({','.join('?' for _ in names)})",list(row.values()))
            added += before is None
    return added


def publish_bar_snapshot(db_path: str, code: str, bars: pd.DataFrame, *, source: str, adjustment: str,
                         adjustment_version: str, evidence: str, reason: str,
                         expected_version: str | None = None, expected_adjustment: str | None = None) -> int:
    if (not source.strip() or any(s.strip().lower() in UNKNOWN for s in source.split('+')) or adjustment not in {'qfq','hfq','none'} or
        not adjustment_version.strip() or adjustment_version=='unknown' or not evidence.strip() or not reason.strip() or bars.empty):
        raise ValueError('完整快照需要来源、复权版本、证据和纠错原因')
    if bars['date'].astype(str).duplicated().any():
        raise ValueError('完整快照包含重复日期')
    fetched=stamp()
    rows=[]
    for _,r in bars.sort_values('date').iterrows():
        row=dict(code=code,date=str(r['date']),**{k:None if pd.isna(r[k]) else float(r[k]) for k in PRICE},
                 source=source,fetched_at=fetched,verification_status='verified',quarantine_reason='',
                 adjustment=adjustment,adjustment_version=adjustment_version,evidence=evidence)
        bad=row_reason(row)
        if bad:
            raise ValueError(f"快照交易日/价格无效（休市或未验证）: {row['date']} {bad}")
        if row['amount'] is None:
            raise ValueError('日线成交额缺少验证值')
        rows.append(row)
    content_hash=snapshot_hash(rows)
    from data.db import _connect
    conn=_connect(db_path)
    try:
        conn.execute('BEGIN IMMEDIATE')
        active=conn.execute('SELECT adjustment,version FROM active_bar_snapshots WHERE code=?',(code,)).fetchone()
        replacing = active is not None and (active['adjustment'],active['version']) != (adjustment,adjustment_version)
        if replacing and expected_version is None:
            raise ValueError('替换完整快照必须提供预期旧版本')
        if expected_version is not None and (not active or active['version']!=expected_version or active['adjustment']!=(expected_adjustment or adjustment)):
            raise ValueError('纠错预期旧版本已变化，请重新核对')
        existing={r['date']:dict(r) for r in conn.execute('SELECT * FROM bars WHERE code=?',(code,))}
        required={day for day,r in existing.items() if not row_reason(r)}
        if required-set(r['date'] for r in rows):
            raise ValueError('快照不完整：缺少现有可交易历史日期')
        used=conn.execute('SELECT content_hash FROM bar_snapshots WHERE code=? AND adjustment=? AND version=?',(code,adjustment,adjustment_version)).fetchone()
        if used:
            if used['content_hash']!=content_hash:
                raise ValueError('复权版本不能重用于不同价格内容')
            if not active or (active['adjustment'],active['version'])!=(adjustment,adjustment_version):
                raise ValueError('旧复权版本不能重新激活，请发布新版本')
            conn.rollback()
            return 0
        # Preserve every superseded value in the same transaction as publication.
        for row in rows:
            revision(conn,existing.get(row['date']),row,'snapshot',reason,evidence)
            names=list(row)
            conn.execute(f"INSERT OR REPLACE INTO bars({','.join(names)}) VALUES({','.join('?' for _ in names)})",list(row.values()))
        conn.execute('INSERT INTO bar_snapshots VALUES(?,?,?,?,?,?)',(code,adjustment,adjustment_version,content_hash,evidence,fetched))
        conn.execute('INSERT OR REPLACE INTO active_bar_snapshots VALUES(?,?,?)',(code,adjustment,adjustment_version))
        conn.commit()
        return len(rows)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_training_bars(db_path: str, code: str, start=None, end=None, *, adjustment: str | None = None,
                       gap_policy: str = 'strict') -> pd.DataFrame:
    if gap_policy not in {'strict', 'segments'}:
        raise ValueError('未知历史缺口处理方式')
    from data.db import _connect, BAR_COLUMNS
    cols=BAR_COLUMNS+list(META)+['source','fetched_at']
    conn=_connect(db_path)
    try:
        if not {'verification_status','adjustment_version'} <= {r[1] for r in conn.execute('PRAGMA table_info(bars)')}:
            return pd.DataFrame(columns=cols)
        frame=pd.read_sql_query('SELECT * FROM trusted_bars WHERE code=? ORDER BY date',conn,params=[code])
        if frame.empty or (adjustment is not None and frame['adjustment'].unique().tolist()!=[adjustment]):
            return pd.DataFrame(columns=cols)
        registered = conn.execute('SELECT s.content_hash FROM bar_snapshots s JOIN active_bar_snapshots a ON s.code=a.code AND s.adjustment=a.adjustment AND s.version=a.version WHERE a.code=?',(code,)).fetchone()
        if not registered or snapshot_hash(frame.to_dict('records')) != registered[0]:
            return pd.DataFrame(columns=cols)
        if any(row_reason(r) for r in frame.to_dict('records')):
            return pd.DataFrame(columns=cols)
        gaps=classify_internal_gaps(conn,dates_by_code={code:set(frame['date'])})
        if gaps and gap_policy == 'strict':
            # Conservative: do not calculate rolling features across missing sessions, even known suspensions.
            return pd.DataFrame(columns=cols)
        if gap_policy == 'segments':
            # Missing observations remain missing, whatever their classification.
            # Segment IDs prevent rolling factors from bridging even unknown gaps.
            from data.trading_calendar import expected_sessions
            sessions = expected_sessions(str(frame['date'].min()), str(frame['date'].max()))
            positions = {day: index for index, day in enumerate(sessions)}
            ordinal = frame['date'].map(positions)
            frame['history_segment'] = ordinal.diff().ne(1).cumsum() - 1
            cols = cols + ['history_segment']
        if start:
            frame=frame[frame['date']>=str(start)]
        if end:
            frame=frame[frame['date']<=str(end)]
        frame['date']=pd.to_datetime(frame['date']).dt.date
        out = frame[cols].reset_index(drop=True)
        out.attrs['internal_gaps'] = gaps
        return out
    except sqlite3.OperationalError as exc:
        if 'no such' in str(exc):
            return pd.DataFrame(columns=cols)
        raise
    finally:
        conn.close()


def audit_baseline(db_path: str) -> dict:
    from data.db import _connect, init_db
    init_db(db_path)
    conn=_connect(db_path)
    try:
        report=dict(recorded_at=stamp(),db_path=str(Path(db_path).resolve()),
                    integrity_check=conn.execute('PRAGMA integrity_check').fetchone()[0])
        for table,prefix,view in [('bars','daily','trusted_bars'),('minute_bars','minute','trusted_minute_bars')]:
            report[prefix+'_rows']=conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            report['trusted_'+prefix+'_rows']=conn.execute(f'SELECT COUNT(*) FROM {view}').fetchone()[0]
            report['quarantined_'+prefix+'_rows']=conn.execute(f"SELECT COUNT(*) FROM {table} WHERE verification_status<>'verified'").fetchone()[0]
            report[prefix+'_quarantine_reasons']=dict(conn.execute(f"SELECT quarantine_reason,COUNT(*) FROM {table} WHERE verification_status<>'verified' GROUP BY quarantine_reason").fetchall())
        from data.timeliness import minute_cache_state
        latest=conn.execute('SELECT date,time FROM minute_bars ORDER BY date DESC,time DESC LIMIT 1').fetchone()
        report['minute_latest_date']=latest[0] if latest else None
        report['minute_latest_time']=latest[1] if latest else None
        report['minute_cache']=minute_cache_state(latest[0] if latest else None,latest[1] if latest else None)
        report['daily_sources']=dict(conn.execute('SELECT source,COUNT(*) FROM bars GROUP BY source').fetchall())
        # sqlite3.Row objects still unpack correctly into code/date values.
        report['internal_gaps']=classify_internal_gaps(conn)
        report['internal_gap_sessions']=sum(g['sessions'] for g in report['internal_gaps'])
        cursor=conn.execute('INSERT INTO baseline_audits(recorded_at,report_json) VALUES(?,?)',(report['recorded_at'],encode(report)))
        report['audit_id']=cursor.lastrowid
        for g in report['internal_gaps']:
            conn.execute('INSERT INTO data_gap_segments(audit_id,code,start,end,sessions,classification,evidence,dates_json) VALUES(?,?,?,?,?,?,?,?)',
                         (report['audit_id'],g['code'],g['start'],g['end'],g['sessions'],g['classification'],g['evidence'],encode(g['dates'])))
        conn.commit()
        return report
    finally:
        conn.close()


def correct_bar_record(db_path: str, code: str, date: str, *, changes: dict, expected_before: dict,
                       reason: str, evidence: str, time: str | None = None) -> dict:
    """纠正原始行并隔离；可信日线必须随后发布新完整版本，不直接更改单根复权状态。"""
    if not changes or not set(changes) <= set(PRICE) or not reason.strip() or not evidence.strip():
        raise ValueError('纠错需要价格字段、原因和证据')
    if not set(changes) <= set(expected_before):
        raise ValueError('必须提供每个纠错字段的预期旧值')
    from data.db import _connect
    conn = _connect(db_path)
    table = 'minute_bars' if time is not None else 'bars'
    where, params = 'code=? AND date=?', [code,date]
    if time is not None:
        where += ' AND time=?'
        params.append(time)
    try:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute(f'SELECT * FROM {table} WHERE {where}',params).fetchone()
        if not row:
            raise ValueError('纠错目标不存在')
        before=dict(row)
        if any(k not in before or before[k]!=v for k,v in expected_before.items()):
            raise ValueError('纠错旧值已变化，存在冲突')
        after={**before,**{k:float(v) if v is not None else None for k,v in changes.items()}}
        bad=row_reason(after,minute=time is not None)
        if bad:
            raise ValueError(f'纠错后价格或交易日无效: {bad}')
        after.update(verification_status='quarantined',quarantine_reason='correction_requires_snapshot_verification',
                     evidence=evidence,fetched_at=stamp())
        revision(conn,before,after,'correction',reason,evidence,'minute' if time else 'daily')
        names=list(after)
        conn.execute(f"UPDATE {table} SET "+','.join(k+'=?' for k in names)+f' WHERE {where}',list(after.values())+params)
        conn.commit()
        return after
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def capture_batch(conn, bars: pd.DataFrame) -> None:
    capture=bars.attrs.get('capture_payload')
    evidence=bars.attrs.get('evidence','')
    if capture and evidence:
        conn.execute('INSERT OR IGNORE INTO data_source_batches VALUES(?,?,?,?)',
                     (evidence,bars.attrs.get('source','unknown'),stamp(),capture))


def load_training_minute_bars(db_path: str, code: str, *, date: str | None = None,
                              live: bool = False, now: dt.datetime | None = None) -> pd.DataFrame:
    """只读取可验证分钟；历史读取不冒充实时，live 模式另外检查每只证券分钟时效。"""
    from data.db import _connect, MINUTE_BAR_COLUMNS
    from data.timeliness import minute_cache_state
    conn=_connect(db_path)
    try:
        sql='SELECT * FROM trusted_minute_bars WHERE code=?'
        params=[code]
        if date:
            sql+=' AND date=?'
            params.append(date)
        frame=pd.read_sql_query(sql+' ORDER BY date,time',conn,params=params)
        if frame.empty:
            return pd.DataFrame(columns=MINUTE_BAR_COLUMNS+list(META))
        if live:
            expected=minute_cache_state(None,None,now=now)['expected_at']
            if expected is None:
                return pd.DataFrame(columns=MINUTE_BAR_COLUMNS+list(META))
            frame=frame[frame['date']==expected[:10]]
            if frame.empty:
                return pd.DataFrame(columns=MINUTE_BAR_COLUMNS+list(META))
        frame=frame[[not row_reason(r,minute=True) for r in frame.to_dict('records')]]
        for day,group in frame.groupby('date'):
            adjusted=group[group['adjustment'].isin(['qfq','hfq'])]
            if not adjusted.empty:
                identity=conn.execute('SELECT s.content_hash FROM minute_snapshots s JOIN active_minute_snapshots a ON s.code=a.code AND s.date=a.date AND s.adjustment=a.adjustment AND s.version=a.version WHERE a.code=? AND a.date=?',(code,day)).fetchone()
                if not identity or minute_snapshot_hash(group.to_dict('records'))!=identity[0]:
                    frame=frame[frame['date']!=day]
        mixed=frame.groupby('date').filter(lambda g:g['adjustment_version'].nunique()!=1 or g['adjustment'].nunique()!=1)
        frame=frame[~frame['date'].isin(mixed['date'])]
        if not frame.empty and (frame['adjustment'].nunique()!=1 or (frame['adjustment'].iloc[0]!='none' and frame['adjustment_version'].nunique()!=1)):
            return pd.DataFrame(columns=MINUTE_BAR_COLUMNS+list(META))
        if live and not frame.empty:
            latest=frame.iloc[-1]
            if minute_cache_state(latest['date'],latest['time'],now=now)['stale']:
                return pd.DataFrame(columns=MINUTE_BAR_COLUMNS+list(META))
        return frame.reset_index(drop=True)
    except sqlite3.OperationalError as exc:
        if 'no such' in str(exc):
            return pd.DataFrame(columns=MINUTE_BAR_COLUMNS+list(META))
        raise
    finally:
        conn.close()
