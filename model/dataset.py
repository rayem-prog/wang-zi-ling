"""训练标签：未来 horizon 日个股收益相对沪深300的超额收益。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from features.alpha import FACTOR_COLUMNS
from features.intraday import INTRADAY_FACTOR_COLUMNS

ALL_FACTOR_COLUMNS = FACTOR_COLUMNS + INTRADAY_FACTOR_COLUMNS


def add_future_excess(df: pd.DataFrame, bench_df: pd.DataFrame, horizon: int = 5) -> pd.DataFrame:
    """df 需含 code,date,close；bench_df 需含 date,close。返回新增 excess_{h} 与 label 的副本。"""
    out = df.copy()
    bench_close = bench_df.set_index("date")["close"]
    frames = []
    for code, g in out.groupby("code", sort=False):
        g = g.sort_values("date").copy()
        closes = g["close"]
        stock_fwd = closes.shift(-horizon) / closes - 1
        b = g["date"].map(bench_close)
        bench_fwd = b.shift(-horizon) / b - 1
        g[f"excess_{horizon}d"] = stock_fwd - bench_fwd
        g["label"] = np.where(g[f"excess_{horizon}d"].notna(), (g[f"excess_{horizon}d"] > 0).astype(int), np.nan)
        frames.append(g)
    return pd.concat(frames, ignore_index=True) if frames else out


def build_training_data(
    features_df: pd.DataFrame,
    bench_df: pd.DataFrame,
    max_date=None,
    feature_cols: list[str] | None = None,
):
    """返回 (X, y, meta)；meta 保留 code/date 便于按日期分组评估。"""
    if feature_cols is None:
        feature_cols = FACTOR_COLUMNS
    df = add_future_excess(features_df, bench_df, horizon=5)
    if max_date is not None:
        df = df[df["date"] <= max_date]

    # Fill any missing requested features with 0.0
    for c in feature_cols:
        if c not in df.columns:
            df[c] = 0.0
        else:
            df[c] = df[c].fillna(0.0)

    df = df.dropna(subset=["label"])
    X = df[feature_cols].astype(float).to_numpy()
    y = df["label"].astype(int).to_numpy()
    meta = df[["code", "date"]].reset_index(drop=True)
    return X, y, meta

