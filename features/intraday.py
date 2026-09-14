"""10 个日内因子计算模块：基于 1 分钟 bar 计算开盘情绪、日内动量、尾盘态度、波动与趋势。"""

from __future__ import annotations

import numpy as np
import pandas as pd

INTRADAY_FACTOR_COLUMNS = [
    "open30_ret",
    "intraday_mom",
    "vwap_dev",
    "late30_ret",
    "intraday_vol",
    "morning_vol_share",
    "late_vol_share",
    "up_minute_vol_ratio",
    "intraday_reversal",
    "minute_trend_r2",
]


def _calc_single_day(bars: pd.DataFrame) -> dict:
    if bars.empty or len(bars) < 2:
        return {col: 0.0 for col in INTRADAY_FACTOR_COLUMNS}

    bars = bars.sort_values("time").reset_index(drop=True)
    open_0 = float(bars["open"].iloc[0])
    close_end = float(bars["close"].iloc[-1])
    tot_vol = float(bars["volume"].sum())

    # 1. open30_ret: 09:30 - 10:00
    sub_open30 = bars[bars["time"] <= "10:00"]
    if not sub_open30.empty and open_0 > 0:
        open30_ret = float(sub_open30["close"].iloc[-1]) / open_0 - 1.0
    else:
        open30_ret = 0.0

    # 2. intraday_mom: close / open - 1
    intraday_mom = (close_end / open_0 - 1.0) if open_0 > 0 else 0.0

    # 3. vwap_dev: close / vwap - 1
    if tot_vol > 0:
        vwap = float((bars["close"] * bars["volume"]).sum()) / tot_vol
        vwap_dev = (close_end / vwap - 1.0) if vwap > 0 else 0.0
    else:
        vwap_dev = 0.0

    # 4. late30_ret: 14:30 - 15:00
    sub_late30 = bars[bars["time"] >= "14:30"]
    if not sub_late30.empty:
        open_late = float(sub_late30["open"].iloc[0])
        late30_ret = (close_end / open_late - 1.0) if open_late > 0 else 0.0
    else:
        late30_ret = 0.0

    # 5. intraday_vol: realized vol from minute returns
    m_rets = bars["close"].pct_change().dropna()
    if len(m_rets) > 1:
        intraday_vol = float(np.sqrt(np.sum(m_rets**2)))
    else:
        intraday_vol = 0.0

    # 6. morning_vol_share: 09:30 - 10:30 vol / tot_vol
    sub_morning = bars[bars["time"] <= "10:30"]
    morning_vol = float(sub_morning["volume"].sum())
    morning_vol_share = (morning_vol / tot_vol) if tot_vol > 0 else 0.0

    # 7. late_vol_share: 14:00 - 15:00 vol / tot_vol
    sub_late = bars[bars["time"] >= "14:00"]
    late_vol = float(sub_late["volume"].sum())
    late_vol_share = (late_vol / tot_vol) if tot_vol > 0 else 0.0

    # 8. up_minute_vol_ratio: volume > mean_vol while price up
    mean_vol = tot_vol / len(bars) if len(bars) > 0 else 0.0
    up_heavy = bars[(bars["close"] >= bars["open"]) & (bars["volume"] > mean_vol)]
    up_minute_vol_ratio = len(up_heavy) / len(bars) if len(bars) > 0 else 0.0

    # 9. intraday_reversal: - (open30_ret * late30_ret)
    intraday_reversal = -(open30_ret * late30_ret)

    # 10. minute_trend_r2: R^2 of close vs time index
    y = bars["close"].values
    n = len(y)
    if n >= 3 and np.std(y) > 1e-8:
        x = np.arange(n)
        corr_matrix = np.corrcoef(x, y)
        r = corr_matrix[0, 1]
        minute_trend_r2 = float(r**2) if not np.isnan(r) else 0.0
    else:
        minute_trend_r2 = 0.0

    return {
        "open30_ret": float(open30_ret),
        "intraday_mom": float(intraday_mom),
        "vwap_dev": float(vwap_dev),
        "late30_ret": float(late30_ret),
        "intraday_vol": float(intraday_vol),
        "morning_vol_share": float(morning_vol_share),
        "late_vol_share": float(late_vol_share),
        "up_minute_vol_ratio": float(up_minute_vol_ratio),
        "intraday_reversal": float(intraday_reversal),
        "minute_trend_r2": float(minute_trend_r2),
    }


def compute_intraday_features(minute_bars: pd.DataFrame) -> pd.DataFrame:
    """对包含一个或多个交易日的分钟线计算日频日内因子。返回每只票每个交易日一行的特征表。"""
    if minute_bars.empty:
        return pd.DataFrame(columns=["code", "date"] + INTRADAY_FACTOR_COLUMNS)

    results = []
    grouped = minute_bars.groupby(["code", "date"])
    for (code, date_val), group in grouped:
        metrics = _calc_single_day(group)
        metrics["code"] = code
        metrics["date"] = date_val
        results.append(metrics)

    res_df = pd.DataFrame(results)
    col_order = ["code", "date"] + INTRADAY_FACTOR_COLUMNS
    return res_df[col_order].sort_values(["code", "date"]).reset_index(drop=True)
