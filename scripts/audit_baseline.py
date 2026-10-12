"""备份后迁移可信基线；只改变验证元数据，不修改价格。可显式发布完整复权快照。"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from data import db
from data.timeliness import exchange_now


def price_digest(path: Path) -> str:
    digest=hashlib.sha256()
    with sqlite3.connect(f'file:{path.resolve()}?mode=ro',uri=True) as conn:
        for table,columns,order in [('bars','code,date,open,high,low,close,volume,amount,source,fetched_at','code,date'),
                                    ('minute_bars','code,date,time,open,high,low,close,volume,amount,source,fetched_at','code,date,time')]:
            existing={r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
            names=','.join(c for c in columns.split(',') if c in existing)
            for row in conn.execute(f'SELECT {names} FROM {table} ORDER BY {order}'):
                digest.update(json.dumps([table,*row],ensure_ascii=False,allow_nan=False).encode())
    return digest.hexdigest()


def migrate_with_backup(path: Path, backup_root: Path) -> dict:
    if not path.is_file():
        raise ValueError(f'数据库不存在: {path}')
    folder=backup_root/('trusted-baseline-'+exchange_now().strftime('%Y%m%d-%H%M%S-%f'))
    folder.mkdir(parents=True,exist_ok=False)
    backup=folder/'market.db'
    with sqlite3.connect(f'file:{path.resolve()}?mode=ro',uri=True) as src,sqlite3.connect(backup) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise ValueError('备份完整性检查失败')
    before=price_digest(backup)
    report=db.audit_baseline(str(path))
    after=price_digest(path)
    report.update(backup=str(backup.resolve()),backup_sha256=hashlib.sha256(backup.read_bytes()).hexdigest(),
                  original_price_digest=before,current_price_digest=after,original_values_unchanged=before==after)
    report_path=folder/'audit.json'
    report['report_path']=str(report_path.resolve())
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    if before!=after:
        raise RuntimeError(f'审计期间价格内容发生变化，需检查并发写入；备份与报告保留于 {folder}')
    if report['integrity_check']!='ok':
        raise RuntimeError('迁移后数据库完整性检查失败')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',default='data/market.db')
    parser.add_argument('--backup-dir',default='../stock-pilot-backups')
    args=parser.parse_args()
    report=migrate_with_backup(Path(args.db),Path(args.backup_dir))
    summary={k:v for k,v in report.items() if k not in {'internal_gaps'}}
    summary['internal_gap_segments']=len(report['internal_gaps'])
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
