"""引擎 B：可解释的技术规则打分，0–100。"""

from __future__ import annotations

import pandas as pd


def _score_row(f: dict) -> float:
    s = 50.0
    s += 15 if f["ma5_gt_ma20"] else -10
    s += 10 if f["ma20_gt_ma60"] else -5
    s += 8 if f["ret_5"] > 0 else -8
    s += 7 if f["ret_20"] > 0 else -7
    vr = f["vol_ratio_5"]
    if 1.0 <= vr <= 2.0:
        s += 5
    elif vr > 2.0:
        s += 2
    elif vr < 0.8:
        s -= 5
    rsi = f["rsi_14"]
    if 45 <= rsi <= 65:
        s += 5
    elif rsi > 75:
        s -= 12
    elif rsi < 25:
        s += 3
    bp = f["boll_pos"]
    if bp < 0.2:
        s += 5
    elif bp > 0.9:
        s -= 8
    return max(0.0, min(100.0, s))


def score_engine_b(features: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in features.iterrows():
        rows.append({"code": r["code"], "date": r["date"], "score_b": round(_score_row(r), 2)})
    return pd.DataFrame(rows)
