"""季度调仓 walk-forward 回测：引擎 A、引擎 B、加权合并各自独立仿真。

实现口径：
- 决策日 = 回测窗口内每 rebalance_days 个交易日；
- 决策日收盘后用当日特征打分，次一交易日开盘按等权买入 top_n；
- 持有到下一决策日开盘卖出（每次换手扣 cfg.cost_rate）；
- 净值记录在每个卖出日，回测页展示 A/B/加权/沪深300 四组净值点；
- 加权权重：首期用 cfg.initial_w_a，之后按上一期两引擎实现收益，
  经 blend_weights.adjusted_weights 更新并裁剪到 [w_min, w_max]。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

import numpy as np
import pandas as pd

from blend import weights as blend_weights
from features.alpha import FACTOR_COLUMNS
from model import dataset as ds
from model import trainer
from strategy import rules

from . import metrics


@dataclass
class BacktestConfig:
    start: str = "2021-01-01"
    end: str = "2024-12-31"
    train_days: int = 750  # 约 3 年（日历日，用于回看训练窗口）
    rebalance_days: int = 63
    top_n: int = 5
    cost_rate: float = 0.002
    cash: float = 100_000.0
    w_min: float = 0.2
    w_max: float = 0.8
    lookback_days: int = 60
    initial_w_a: float = 0.5


@dataclass
class BacktestResult:
    equity: pd.Series
    trades: pd.DataFrame = field(default_factory=pd.DataFrame)
    stats: dict = field(default_factory=dict)


def _to_date(x) -> object:
    return pd.Timestamp(x).date()


def _decision_positions(dates: list, cfg: BacktestConfig) -> list[int]:
    """返回基准交易日列表中的决策位置 p；要求 p + rebalance_days 仍在窗口内（可完成一个持有期）。"""
    start = _to_date(cfg.start)
    end = _to_date(cfg.end)
    first = next((i for i, d in enumerate(dates) if d >= start), None)
    last = next((i for i in range(len(dates) - 1, -1, -1) if dates[i] <= end), None)
    if first is None or last is None:
        return []
    return [p for p in range(first, last + 1, cfg.rebalance_days) if p + cfg.rebalance_days <= last]


def score_engine_a_at(features_df: pd.DataFrame, bench_df: pd.DataFrame, d, cfg: BacktestConfig) -> pd.DataFrame:
    today = features_df[features_df["date"] == d]
    if today.empty:
        return pd.DataFrame(columns=["code", "date", "score"])
    train_start = d - timedelta(days=int(cfg.train_days * 1.45))
    train_end = d - timedelta(days=7)  # 避免训练标签落在决策日之后 5 日窗口内
    train = features_df[(features_df["date"] >= train_start) & (features_df["date"] <= train_end)]
    X, y, _ = ds.build_training_data(train, bench_df)
    if len(X) < 100:
        return pd.DataFrame({"code": today["code"].tolist(), "date": [d] * len(today), "score": [50.0] * len(today)})
    model = trainer.train_model(X, y, seed=42)
    Xt = today[FACTOR_COLUMNS].astype(float).to_numpy()
    return pd.DataFrame(
        {"code": today["code"].tolist(), "date": [d] * len(today), "score": trainer.predict_scores(model, Xt)}
    )


def score_engine_b_at(features_df: pd.DataFrame, d) -> pd.DataFrame:
    today = features_df[features_df["date"] == d]
    if today.empty:
        return pd.DataFrame(columns=["code", "date", "score"])
    out = rules.score_engine_b(today.reset_index(drop=True))
    return out.rename(columns={"score_b": "score"})[["code", "date", "score"]]


def _period_return(score_df: pd.DataFrame, features_df: pd.DataFrame, dates: list, p: int, cfg: BacktestConfig) -> float:
    """决策位置 p：次一交易日开盘买入，p+rebalance_days 交易日开盘卖出。"""
    buy_date = dates[p + 1]
    sell_date = dates[p + cfg.rebalance_days]
    top = score_df.sort_values("score", ascending=False).head(cfg.top_n)
    rets = []
    for _, row in top.iterrows():
        code = row["code"]
        buy = features_df[(features_df["code"] == code) & (features_df["date"] == buy_date)]
        sell = features_df[(features_df["code"] == code) & (features_df["date"] == sell_date)]
        if buy.empty or sell.empty:
            continue
        prev = features_df[(features_df["code"] == code) & (features_df["date"] == dates[p])]
        if not prev.empty and buy["open"].iloc[0] >= prev["close"].iloc[0] * 1.098:
            continue  # 开盘涨停（≥9.8%）视为无法买入
        rets.append(float(sell["open"].iloc[0] / buy["open"].iloc[0] - 1 - cfg.cost_rate))
    return float(np.mean(rets)) if rets else 0.0


def run_walk_forward(features_df: pd.DataFrame, bench_df: pd.DataFrame, cfg: BacktestConfig, engine_settings=None) -> dict:
    dates = sorted(bench_df["date"].tolist())
    positions = _decision_positions(dates, cfg)
    if not positions:
        raise ValueError("回测窗口内没有可执行的完整调仓周期，请检查 start/end/rebalance_days")
    eq_a = eq_b = eq_blend = cfg.cash
    pts_a: list[tuple] = []
    pts_b: list[tuple] = []
    pts_blend: list[tuple] = []
    w_history: list[dict] = []
    w_a = cfg.initial_w_a
    for p in positions:
        d = dates[p]
        w_history.append({"date": str(d), "w_a_used": round(w_a, 4), "w_b_used": round(1 - w_a, 4)})
        sa = score_engine_a_at(features_df, bench_df, d, cfg)
        sb = score_engine_b_at(features_df, d)
        r_a = _period_return(sa, features_df, dates, p, cfg)
        r_b = _period_return(sb, features_df, dates, p, cfg)
        eq_a *= 1 + r_a
        eq_b *= 1 + r_b
        merged = sa.merge(sb, on=["code", "date"], how="outer").fillna({"score_x": 50.0, "score_y": 50.0})
        merged = merged.rename(columns={"score_x": "score_a", "score_y": "score_b"})
        merged = blend_weights.merge_scores(merged, w_a)
        s_blend = merged[["code", "date", "score_blend"]].rename(columns={"score_blend": "score"})
        r_blend = _period_return(s_blend, features_df, dates, p, cfg)
        eq_blend *= 1 + r_blend
        sell_date = dates[p + cfg.rebalance_days]
        pts_a.append((sell_date, eq_a))
        pts_b.append((sell_date, eq_b))
        pts_blend.append((sell_date, eq_blend))
        w_a, _ = blend_weights.adjusted_weights(r_a, r_b, w_a, cfg.w_min, cfg.w_max)
    equity_a = pd.Series([v for _, v in pts_a], index=pd.DatetimeIndex([d for d, _ in pts_a]))
    equity_b = pd.Series([v for _, v in pts_b], index=pd.DatetimeIndex([d for d, _ in pts_b]))
    equity_blend = pd.Series([v for _, v in pts_blend], index=pd.DatetimeIndex([d for d, _ in pts_blend]))
    equity_bench = metrics.benchmark_equity(bench_df, start=cfg.start, end=cfg.end, cash=cfg.cash)
    results = {
        "a": BacktestResult(equity=equity_a),
        "b": BacktestResult(equity=equity_b),
        "blend": BacktestResult(equity=equity_blend),
        "benchmark": BacktestResult(equity=equity_bench),
    }
    for key, res in results.items():
        res.stats = metrics.compute_metrics(res.equity, results["benchmark"].equity)
    results["blend"].stats["w_history"] = w_history
    return results


def check_intraday_qualification(v1_blend_stats: dict, v2_blend_stats: dict, bench_stats: dict) -> bool:
    """
    检查日内因子是否达到正式启用门槛：
    1. v2 加权收益跑赢基准：v2_ret > bench_ret
    2. 最大回撤相对 v1 劣化不超过 2 个百分点：(v2_dd - v1_dd) <= 0.02
    """
    v2_ret = float(v2_blend_stats.get("annual_return", 0.0))
    bench_ret = float(bench_stats.get("annual_return", 0.0))
    v1_dd = abs(float(v1_blend_stats.get("max_drawdown", 0.0)))
    v2_dd = abs(float(v2_blend_stats.get("max_drawdown", 0.0)))

    outperforms_bench = v2_ret > bench_ret
    drawdown_ok = (v2_dd - v1_dd) <= 0.02
    return outperforms_bench and drawdown_ok

