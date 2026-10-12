"""逐交易日核对内部缺口，按分类及证据分段；未知原因不推断停牌。"""
from __future__ import annotations

import json
from collections import defaultdict

from data.security_master import inactive_status
from data.trading_calendar import expected_sessions


def classify_internal_gaps(conn, *, dates_by_code: dict[str,set[str]] | None = None) -> list[dict]:
    dates_by_code = dates_by_code if dates_by_code is not None else defaultdict(set)
    if not dates_by_code:
        for code, date in conn.execute("SELECT code,date FROM bars ORDER BY code,date"):
            dates_by_code[code].add(date)
    errors = defaultdict(list)
    for row in conn.execute("SELECT id,code,start_date,end_date,error FROM data_fetch_log WHERE kind='daily' AND status='error' ORDER BY id DESC"):
        errors[row[1]].append(row)
    segments = []
    for code, dates in sorted(dates_by_code.items()):
        if not dates:
            continue
        current = None
        for day in expected_sessions(min(dates),max(dates)):
            if day in dates:
                current = None
                continue
            inactive = inactive_status(code,day)
            error = next((r for r in errors[code] if r[2] <= day <= r[3]),None)
            if inactive:
                classification, evidence = "confirmed_"+inactive['status'], inactive['source']
            elif error:
                classification, evidence = "source_error",json.dumps({"fetch_log_id":error[0],"error":error[4]},ensure_ascii=False)
            else:
                classification, evidence = "unexplained_gap", ""
            if current is not None and (current['classification'],current['evidence']) == (classification,evidence):
                current['end'],current['sessions'] = day,current['sessions']+1
                current['dates'].append(day)
            else:
                current = dict(code=code,start=day,end=day,sessions=1,dates=[day],classification=classification,evidence=evidence)
                segments.append(current)
    return segments
