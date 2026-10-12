"""Point-in-time daily replay of the production decision and execution pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field, replace

import pandas as pd

from config.settings import Settings
from strategy.decision import asof_frame, decide, engine_a
from strategy.macro import load_event_calendar
from strategy.evaluation import PredictionLedger
from strategy.execution import Portfolio
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
    min_train_rows: int = 5000


@dataclass
class BacktestResult:
    equity: pd.Series
    trades: pd.DataFrame = field(default_factory=pd.DataFrame)
    stats: dict = field(default_factory=dict)
    decisions: list[dict] = field(default_factory=list)
    valuations: pd.DataFrame = field(default_factory=pd.DataFrame)



def score_engine_a_at(features_df, bench_df, d, cfg):
    scores, _ = engine_a(features_df, bench_df, d, cfg.train_days, cfg.min_train_rows)
    return scores.rename(columns={'score_a':'score'})


def score_engine_b_at(features_df, d):
    from strategy import rules
    today = asof_frame(features_df, d)
    today = today[today.date == pd.Timestamp(d).date()]
    if today.empty:
        return pd.DataFrame(columns=['code','date','score'])
    return rules.score_engine_b(today).rename(columns={'score_b':'score'})


def run_walk_forward(features_df, bench_df, cfg: BacktestConfig, engine_settings=None, settings=None):
    if cfg.rebalance_days < 1 or cfg.top_n < 1 or pd.Timestamp(cfg.end) < pd.Timestamp(cfg.start):
        raise ValueError('回测日期、调仓周期或持仓数量无效')
    features = asof_frame(features_df, cfg.end)
    benchmark = asof_frame(bench_df, cfg.end)
    from data.trading_calendar import closure_kind
    benchmark = benchmark[benchmark.date.map(lambda d: closure_kind(d) is None)]
    days = [d for d in benchmark.date if d >= pd.Timestamp(cfg.start).date()]
    if not days:
        raise ValueError('回测窗口内没有交易日')
    base = settings or Settings()
    engine_settings = engine_settings or (base.engine if settings is not None else replace(base.engine, w_a=cfg.initial_w_a, w_b=1-cfg.initial_w_a,
                                                 w_min=cfg.w_min, w_max=cfg.w_max))
    base = replace(base, engine=engine_settings, account=replace(base.account,cash=cfg.cash,max_positions=cfg.top_n))
    events_cfg = load_event_calendar()
    accounts = {e:Portfolio(cfg.cash,cfg.cost_rate) for e in ('a','b','blend')}
    points = {e:[] for e in accounts}
    decisions = {e:[] for e in accounts}
    pending = {}
    weight_history = []
    ledger = PredictionLedger()
    daily_bars = {d:g for d,g in features.groupby('date')}
    try:
        for i, day in enumerate(days):
            bars = daily_bars.get(day, pd.DataFrame())
            for e, account in accounts.items():
                prior = pending.pop(e,None)
                if prior:
                    account.execute(prior.orders.assign(date=prior.date),bars,str(day),
                                    min(prior.macro.max_total_position,prior.parameters["risk"]["max_total_position"]),base.account.max_positions)
                account.mark(bars)
                points[e].append(dict(date=day,equity=account.equity,cash=account.cash,
                                     stale_codes=','.join(account.stale_codes)))
            weight_eval = ledger.advance(features,benchmark,day,base.engine)
            wa = weight_eval['new_w_a']
            for e, account in accounts.items():
                context = replace(base,account=replace(base.account,cash=account.cash))
                decision = decide(features,benchmark,day,context,wa,account.holdings(),
                                  train_days=cfg.train_days,min_train_rows=cfg.min_train_rows,engine=e,events_cfg=events_cfg,allow_new_entries=i % cfg.rebalance_days == 0)
                decisions[e].append(decision.to_payload())
                pending[e] = decision
                if e == 'blend':
                    snapshot = decision.to_payload()
                    snapshot['benchmark_close'] = float(benchmark.loc[benchmark.date == day,'close'].iloc[0])
                    ledger.freeze(str(day),snapshot)
                    weight_history.append(dict(date=str(day),w_a_used=wa,w_b_used=round(1-wa,4)))
    finally:
        ledger.close()
    bench_equity = metrics.benchmark_equity(benchmark,start=days[0],end=days[-1],cash=cfg.cash)
    results = {'benchmark':BacktestResult(equity=bench_equity)}
    for e, account in accounts.items():
        valuations = pd.DataFrame(points[e]).set_index('date')
        valuations.index = pd.DatetimeIndex(valuations.index)
        results[e] = BacktestResult(equity=valuations.equity,trades=pd.DataFrame(account.audit),
                                   decisions=decisions[e],valuations=valuations)
    for res in results.values():
        res.stats = metrics.compute_metrics(res.equity,bench_equity,initial_cash=cfg.cash)
    results['blend'].stats['w_history'] = weight_history
    return results


def check_intraday_qualification(v1_blend_stats: dict, v2_blend_stats: dict, bench_stats: dict) -> bool:
    """
    检查日内因子是否达到正式启用门槛：
    1. v2 加权收益跑赢基准：v2_ret > bench_ret
    2. 最大回撤相对 v1 劣化不超过 2 个百分点：(v2_dd - v1_dd) <= 0.02
    """
    v2_ret = float(v2_blend_stats.get("ann_return", v2_blend_stats.get("annual_return", 0.0)))
    bench_ret = float(bench_stats.get("ann_return", bench_stats.get("annual_return", 0.0)))
    v1_dd = abs(float(v1_blend_stats.get("max_drawdown", 0.0)))
    v2_dd = abs(float(v2_blend_stats.get("max_drawdown", 0.0)))

    outperforms_bench = v2_ret > bench_ret
    drawdown_ok = (v2_dd - v1_dd) <= 0.02
    return outperforms_bench and drawdown_ok

