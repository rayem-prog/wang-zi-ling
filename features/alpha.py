"""18 个技术因子：动量、均线、波动率、RSI、布林、量价。全部只用历史数据。"""

from __future__ import annotations

import pandas as pd

FACTOR_COLUMNS = [
    "ret_1", "ret_5", "ret_10", "ret_20", "ret_60",
    "ma_bias_5", "ma_bias_10", "ma_bias_20", "ma_bias_60",
    "ma5_gt_ma20", "ma20_gt_ma60",
    "vol_20", "rsi_14", "boll_pos", "vol_ratio_5", "amount_ratio_5",
    "high_low_pos_20", "range_20",
]


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = (-delta.clip(upper=0)).rolling(window).mean()
    rs = gain / loss.replace(0, pd.NA)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def compute_features(bars: pd.DataFrame) -> pd.DataFrame:
    if len(bars) < 61:
        raise ValueError("需要至少 61 行日线数据")
    df = bars.copy().sort_values("date").reset_index(drop=True)
    close = df["close"]
    for k in (1, 5, 10, 20, 60):
        df[f"ret_{k}"] = close.pct_change(k)
    for k in (5, 10, 20, 60):
        ma = close.rolling(k).mean()
        df[f"ma_bias_{k}"] = close / ma - 1
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    df["ma5_gt_ma20"] = (ma5 > ma20).astype(int)
    df["ma20_gt_ma60"] = (ma20 > ma60).astype(int)
    df["vol_20"] = close.pct_change().rolling(20).std()
    df["rsi_14"] = _rsi(close, 14)
    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    upper = mid + 2 * std
    lower = mid - 2 * std
    df["boll_pos"] = (close - lower) / (upper - lower)
    df["vol_ratio_5"] = df["volume"] / df["volume"].rolling(5).mean() - 1
    df["amount_ratio_5"] = df["amount"] / df["amount"].rolling(5).mean() - 1
    hh = df["high"].rolling(20).max()
    ll = df["low"].rolling(20).min()
    df["high_low_pos_20"] = (close - ll) / (hh - ll)
    df["range_20"] = (hh - ll) / close
    return df
