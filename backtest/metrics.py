"""回测绩效：基准净值、收益/回撤/夏普/胜率、RankIC。"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def benchmark_equity(bench_df: pd.DataFrame, start=None, end=None, cash: float = 100_000.0) -> pd.Series:
    b = bench_df.sort_values("date")
    if start:
        b = b[b["date"] >= pd.Timestamp(start).date()]
    if end:
        b = b[b["date"] <= pd.Timestamp(end).date()]
    eq = cash * b["close"] / float(b["close"].iloc[0])
    eq.index = pd.to_datetime(b["date"])
    return eq


def compute_metrics(equity: pd.Series, bench_equity: pd.Series) -> dict:
    eq = equity.dropna()
    beq = bench_equity.reindex(eq.index).ffill()
    rets = eq.pct_change().dropna()
    total_return = eq.iloc[-1] / eq.iloc[0] - 1
    n = len(rets)
    ann_return = (1 + total_return) ** (TRADING_DAYS / max(n, 1)) - 1 if total_return > -1 else -1.0
    sharpe = float(rets.mean() / rets.std() * np.sqrt(TRADING_DAYS)) if rets.std() > 0 else 0.0
    dd = eq / eq.cummax() - 1
    max_drawdown = float(dd.min())
    win_rate = float((rets > 0).mean()) if n else 0.0
    bench_total = beq.iloc[-1] / beq.iloc[0] - 1 if len(beq) > 1 else 0.0
    return {
        "total_return": float(total_return),
        "ann_return": float(ann_return),
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "win_rate": win_rate,
        "excess_total_return": float(total_return - bench_total),
    }


def rank_ic(scored: pd.DataFrame) -> float:
    """scored 需含 date,code,score,excess；返回逐日 spearman 相关均值。"""
    ics = []
    for _, g in scored.dropna(subset=["score", "excess"]).groupby("date"):
        if len(g) < 3:
            continue
        rank_s = g["score"].rank().to_numpy()
        rank_e = g["excess"].rank().to_numpy()
        if rank_s.std() == 0 or rank_e.std() == 0:
            continue
        ics.append(float(np.corrcoef(rank_s, rank_e)[0, 1]))
    return float(np.mean(ics)) if ics else float("nan")
