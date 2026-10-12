"""Observed indicators with dates, not invented factor contributions or forecasts."""
import math
import pandas as pd


def observed_evidence(features, as_of, codes):
    if features.empty:
        return []
    history = features[pd.to_datetime(features.date) <= pd.Timestamp(as_of)].copy()
    items = []
    for code in codes:
        rows = history[history.code == code].sort_values('date')
        if rows.empty:
            items.append(dict(code=code, unavailable=True, reason='缺少可信历史因子'))
            continue
        row = rows.iloc[-1]
        item = dict(code=code, history_start=str(rows.date.iloc[0]), history_end=str(row.date),
                    feature_sessions=len(rows), stale=str(row.date)[:10] != str(as_of)[:10])
        for column, key, scale in [('close','close',1),('ret_5','ret_5_pct',100),
                ('ret_20','ret_20_pct',100),('ret_60','ret_60_pct',100),('vol_20','vol_20_pct',100),
                ('ma_bias_20','ma_bias_20_pct',100),('rsi_14','rsi_14',1)]:
            value = row.get(column)
            item[key] = round(float(value)*scale,4) if pd.notna(value) and math.isfinite(float(value)) else None
        item['source'] = str(row.get('source','unknown'))
        items.append(item)
    return items
