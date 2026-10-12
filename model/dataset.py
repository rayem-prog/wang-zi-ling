"""训练标签：未来 horizon 日个股收益相对沪深300的超额收益。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from features.alpha import FACTOR_COLUMNS
from features.intraday import INTRADAY_FACTOR_COLUMNS

ALL_FACTOR_COLUMNS = FACTOR_COLUMNS + INTRADAY_FACTOR_COLUMNS


def add_future_excess(df: pd.DataFrame, bench_df: pd.DataFrame, horizon: int = 5) -> pd.DataFrame:
    """df 需含 code,date,close；bench_df 需含 date,close。返回新增 excess_{h} 与 label 的副本。"""
    from data.trading_calendar import closure_kind, expected_sessions
    def session_rows(frame):
        valid = frame["date"].map(lambda value: closure_kind(pd.Timestamp(value).date()) is None)
        return frame[valid].copy()
    out = session_rows(df)
    bench = session_rows(bench_df).sort_values("date").drop_duplicates("date")
    if not bench.empty:
        days = [pd.Timestamp(day).date() for day in expected_sessions(str(bench["date"].min()), str(bench["date"].max()))]
        bench["date"] = pd.to_datetime(bench["date"]).dt.date
        bench = bench.set_index("date").reindex(days).rename_axis("date").reset_index()
    out["date"] = pd.to_datetime(out["date"]).dt.date
    bench_close = bench.set_index("date")["close"]
    targets = pd.Series(bench["date"].shift(-horizon).values, index=bench["date"])
    sessions = bench["date"].tolist()
    positions = {day:i for i,day in enumerate(sessions)}
    frames = []
    for code, g in out.groupby("code", sort=False):
        g = g.sort_values("date").copy()
        closes = g.set_index("date")["close"]
        target = g["date"].map(targets)
        stock_fwd = target.map(closes) / g["close"] - 1
        bench_fwd = target.map(bench_close) / g["date"].map(bench_close) - 1
        complete = g["date"].map(lambda day: day in positions and positions[day]+horizon < len(sessions)
            and all(d in closes.index and pd.notna(bench_close.get(d)) for d in sessions[positions[day]:positions[day]+horizon+1]))
        g[f"excess_{horizon}d"] = (stock_fwd - bench_fwd).where(complete)
        g["label"] = np.where(g[f"excess_{horizon}d"].notna(), (g[f"excess_{horizon}d"] > 0).astype(int), np.nan)
        frames.append(g)
    return pd.concat(frames, ignore_index=True) if frames else out.assign(label=np.nan)



def build_training_data(
    features_df: pd.DataFrame,
    bench_df: pd.DataFrame,
    max_date=None,
    feature_cols: list[str] | None = None,
    require_verified: bool = False,
):
    """返回 (X, y, meta)；meta 保留 code/date 便于按日期分组评估。"""
    if require_verified:
        for frame in (features_df, bench_df):
            required = {"verification_status", "adjustment", "adjustment_version", "evidence"}
            if not required <= set(frame.columns) or not frame["verification_status"].eq("verified").all():
                raise ValueError("训练数据缺少可信验证证据")
            if frame["evidence"].fillna("").eq("").any() or frame["adjustment_version"].isin(["unknown", ""]).any():
                raise ValueError("训练数据缺少可信复权版本")
            if not frame["adjustment"].isin(["qfq", "hfq", "none"]).all() or ("code" in frame and ((frame.groupby("code")["adjustment_version"].nunique() > 1).any() or (frame.groupby("code")["adjustment"].nunique() > 1).any())):
                raise ValueError("训练不能混合复权版本")
    if feature_cols is None:
        feature_cols = FACTOR_COLUMNS
    if max_date is not None:
        features_df = features_df[pd.to_datetime(features_df["date"]) <= pd.Timestamp(max_date)]
        bench_df = bench_df[pd.to_datetime(bench_df["date"]) <= pd.Timestamp(max_date)]
    df = add_future_excess(features_df, bench_df, horizon=5)

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

