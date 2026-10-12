"""AI 历史推演与盈利探究沙盒引擎 (Historical Replay & Time-Accelerated Simulation Sandbox)。

支持：
1. 使用已有真实历史行情进行推演，不生成行情种子；
2. AI 多方案选股（强势动量突破、稳健价值低估、双引擎均衡）与自定义选股策略；
3. 时间加速步进（逐日推进、多倍速加速、全周期秒级推演）；
4. 盈利水平与绩效探究（策略净值 vs 沪深300、Alpha超额收益、最大回撤、胜率、盈亏比、夏普比率）。
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd
from strategy import risk, macro
from strategy.decision import decide
from strategy.evaluation import PredictionLedger
from strategy.execution import Portfolio
from features.alpha import compute_features
from config.settings import load_settings

DB_PATH = "data/market.db"


def _market_db_path() -> str:
    from config.settings import load_settings
    return load_settings().data.db_path

# 历史沙盒推演股票池配置（涵盖低价、中价、成长与核心蓝筹）
SANDBOX_STOCK_POOL = [
    # 黄金低价池 (≤¥20)
    {"code": "000725", "name": "京东方A", "base_price": 3.80, "volatility": 0.022, "drift": 0.0006, "tier": "low"},
    {"code": "601988", "name": "中国银行", "base_price": 4.50, "volatility": 0.012, "drift": 0.0004, "tier": "low"},
    {"code": "600019", "name": "宝钢股份", "base_price": 5.80, "volatility": 0.018, "drift": 0.0005, "tier": "low"},
    {"code": "000100", "name": "TCL科技", "base_price": 4.10, "volatility": 0.025, "drift": 0.0007, "tier": "low"},
    {"code": "000001", "name": "平安银行", "base_price": 9.80, "volatility": 0.019, "drift": 0.0003, "tier": "low"},
    {"code": "601899", "name": "紫金矿业", "base_price": 12.50, "volatility": 0.024, "drift": 0.0011, "tier": "low"},
    {"code": "600030", "name": "中信证券", "base_price": 18.20, "volatility": 0.023, "drift": 0.0005, "tier": "low"},
    {"code": "601398", "name": "工商银行", "base_price": 5.20, "volatility": 0.011, "drift": 0.0004, "tier": "low"},

    # 稳健中价池 (¥20~¥50)
    {"code": "600900", "name": "长江电力", "base_price": 23.00, "volatility": 0.012, "drift": 0.0008, "tier": "mid"},
    {"code": "600036", "name": "招商银行", "base_price": 29.50, "volatility": 0.018, "drift": 0.0005, "tier": "mid"},
    {"code": "002415", "name": "海康威视", "base_price": 31.00, "volatility": 0.022, "drift": 0.0004, "tier": "mid"},
    {"code": "002475", "name": "立讯精密", "base_price": 32.00, "volatility": 0.026, "drift": 0.0009, "tier": "mid"},
    {"code": "601318", "name": "中国平安", "base_price": 42.00, "volatility": 0.020, "drift": 0.0004, "tier": "mid"},
    {"code": "000333", "name": "美的集团", "base_price": 46.00, "volatility": 0.017, "drift": 0.0007, "tier": "mid"},

    # 成长中高价池 (¥50~¥100)
    {"code": "600276", "name": "恒瑞医药", "base_price": 44.00, "volatility": 0.023, "drift": 0.0006, "tier": "high"},
    {"code": "300124", "name": "汇川技术", "base_price": 55.00, "volatility": 0.027, "drift": 0.0008, "tier": "high"},
    {"code": "600519", "name": "贵州茅台", "base_price": 1580.00, "volatility": 0.016, "drift": 0.0003, "tier": "top"},
    {"code": "300750", "name": "宁德时代", "base_price": 180.00, "volatility": 0.028, "drift": 0.0007, "tier": "top"},
    {"code": "002594", "name": "比亚迪", "base_price": 220.00, "volatility": 0.025, "drift": 0.0008, "tier": "top"},
]


def seed_historical_market_data(force: bool = False, db_path: str | None = None) -> dict[str, Any]:
    """检查历史行情是否可用于沙盒；绝不向真实行情表注入模拟价格。"""
    from data import db

    settings = load_settings()
    db_path = db_path or settings.data.db_path
    frames = []
    if Path(db_path).is_file():
        # Match the session's trusted reader, including adjustment and snapshot checks.
        with sqlite3.connect(f'{Path(db_path).resolve().as_uri()}?mode=ro', uri=True) as conn:
            has_bars = conn.execute("SELECT 1 FROM sqlite_master WHERE name='bars'").fetchone()
            codes = [row[0] for row in conn.execute('SELECT DISTINCT code FROM bars')] if has_bars else []
        for code in codes:
            if code.startswith(('sh000', 'sz399')) and code != settings.data.index_symbol:
                continue
            adjustment = 'none' if code == settings.data.index_symbol else settings.data.adjust
            bars = db.load_training_bars(db_path, code, adjustment=adjustment)
            if not bars.empty:
                frames.append(bars)
    trusted = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=['code', 'date'])
    count, stock_cnt = len(trusted), trusted.code.nunique()
    min_d = str(trusted.date.min()) if count else None
    max_d = str(trusted.date.max()) if count else None
    ready = bool(count > 1000 and stock_cnt >= 2 and settings.data.index_symbol in set(trusted.code))
    return {
        "status": "already_seeded" if ready else "insufficient_history",
        "message": (f"历史行情库覆盖 {stock_cnt} 只标的，区间 {min_d} 至 {max_d}，共 {count} 根日K线"
                    if ready else "可信历史行情不足，沙盒无法运行；请先补采并核验行情"),
        "total_bars": count,
        "stock_count": stock_cnt,
        "min_date": min_d,
        "max_date": max_d,
        "source": "trusted_database_market_bars",
        "simulated": False,
    }


# AI 策略方案预设参数
SCHEME_PRESETS = {
    "momentum": {
        "name": "🚀 AI 强势动量突破方案",
        "desc": "共用双引擎评分，最多4只持仓，每5日建仓，5%止损、15%止盈；单票受全局上限约束。",
        "max_positions": 4,
        "max_single_weight": 0.25,
        "stop_loss": 0.05,
        "take_profit": 0.15,
        "factor_focus": "momentum",
        "pool_filter": "all",
        "rebalance_interval": 5,
    },
    "value": {
        "name": "🛡️ AI 低价稳健方案",
        "desc": "共用双引擎评分，仅选历史当日价格≤20元标的，单票上限15%，每10日建仓，7%止损、12%止盈。",
        "max_positions": 6,
        "max_single_weight": 0.15,
        "stop_loss": 0.07,
        "take_profit": 0.12,
        "factor_focus": "value",
        "pool_filter": "low",
        "rebalance_interval": 10,
    },
    "balanced": {
        "name": "⚖️ AI 双引擎多因子均衡方案",
        "desc": "LightGBM 与线性多因子动态加权，均衡配置价值与成长，单票上限 20%，6% 止损，15% 止盈。",
        "max_positions": 5,
        "max_single_weight": 0.20,
        "stop_loss": 0.06,
        "take_profit": 0.15,
        "factor_focus": "balanced",
        "pool_filter": "all",
        "rebalance_interval": 7,
    },
    "custom": {
        "name": "🛠️ 自定义选股方案",
        "desc": "由用户自由设定资金门槛、自选股范围、单票仓位、止损止盈参数进行个性化历史推演。",
        "max_positions": 5,
        "max_single_weight": 0.20,
        "stop_loss": 0.05,
        "take_profit": 0.15,
        "factor_focus": "balanced",
        "pool_filter": "all",
        "rebalance_interval": 5,
    }
}


class HistoricalReplaySandbox:
    """管理单次历史数据推演与时间加速运行的沙盒会话。"""

    def __init__(
        self,
        scheme: str = "balanced",
        start_date: str = "2023-01-03",
        end_date: str = "2024-12-31",
        initial_cash: float = 1_000_000.0,
        custom_config: dict[str, Any] | None = None,
        watchlist_codes: list[str] | None = None,
        progress=None,
    ):
        self.scheme_key = scheme if scheme in SCHEME_PRESETS else "balanced"
        preset = SCHEME_PRESETS[self.scheme_key].copy()
        if custom_config and self.scheme_key == "custom":
            preset.update(custom_config)

        self.cfg = preset
        self.start_date = start_date
        self.end_date = end_date
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.watchlist_codes = watchlist_codes or []
        self.settings = load_settings()
        self.events_cfg = macro.load_event_calendar()
        self.db_path = self.settings.data.db_path
        from data import db
        db.init_db(self.db_path)

        # Freeze data and settings for the session, using the production trusted reader.
        stock_frames = []
        feature_frames = []
        filter_type = self.cfg.get('pool_filter', 'all')
        names = {r['code']:r['name'] for r in SANDBOX_STOCK_POOL}
        codes = sorted(db.list_codes(self.db_path))
        for idx, code in enumerate(codes):
            if progress:
                progress('核验历史行情并计算因子',idx,len(codes))
            if code == self.settings.data.index_symbol or code.startswith(('sh000','sz399')):
                continue
            if filter_type == 'watchlist' and code not in self.watchlist_codes:
                continue
            bars = db.load_training_bars(self.db_path,code,end=end_date,adjustment=self.settings.data.adjust)
            if bars.empty:
                continue
            bars['name'] = names.get(code,code)
            stock_frames.append(bars)
            if len(bars) >= 61:
                feature_frames.append(compute_features(bars).iloc[60:])
        self._bars = pd.concat(stock_frames,ignore_index=True) if stock_frames else pd.DataFrame()
        self._features = pd.concat(feature_frames,ignore_index=True) if feature_frames else pd.DataFrame(columns=['code','date','close','vol_20'])
        self._bench = db.load_training_bars(self.db_path,self.settings.data.index_symbol,end=end_date,adjustment='none')
        self.data_info = dict(stock_count=len(stock_frames), total_bars=len(self._bars)+len(self._bench),
            min_date=str(self._bars.date.min()) if not self._bars.empty else None,
            max_date=str(self._bars.date.max()) if not self._bars.empty else None,
            simulated=False, source='trusted_database_market_bars')
        self.trading_days = self._get_trading_days(start_date,end_date)
        self.current_idx = 0
        if int(self.cfg.get('rebalance_interval',5)) < 1 or int(self.cfg.get('max_positions',5)) < 1:
            raise ValueError('调仓周期和最大持仓数必须为正数')
        self.settings = replace(self.settings,
            account=replace(self.settings.account,cash=self.initial_cash,max_positions=int(self.cfg.get('max_positions',5))),
            risk=replace(self.settings.risk,
                max_single_position=min(self.cfg.get('max_single_weight',.2),self.settings.risk.max_single_position),
                stop_loss=self.cfg.get('stop_loss',.05),take_profit=self.cfg.get('take_profit',.15)))
        self._account = Portfolio(self.initial_cash,float(self.cfg.get('cost_rate',.002)))
        self._ledger = PredictionLedger(check_same_thread=False)
        self._pending = None
        self._last_decision = None
        self.positions = self._account.positions
        self.trades = []
        self.order_audit = self._account.audit
        self.decisions = []
        self.equity_curve = []
        self.bench_start_price = None

    def _get_trading_days(self,start,end):
        if self._bench.empty or self._bars.empty:
            return []
        stock_dates = set(self._bars.date.astype(str))
        from data.trading_calendar import closure_kind
        days = [str(d) for d in self._bench.date if start <= str(d) <= end and closure_kind(pd.Timestamp(d).date()) is None]
        return days if stock_dates.intersection(days) else []

    def _get_prices_at(self,date_str):
        prices = {}
        if not self._bars.empty:
            rows = self._bars[self._bars.date.astype(str) == date_str]
            prices = dict(zip(rows.code,rows.close))
        if not self._bench.empty:
            row = self._bench[self._bench.date.astype(str) == date_str]
            if not row.empty:
                prices[self.settings.data.index_symbol] = float(row.close.iloc[0])
        return prices

    def _historical_decision(self,date_str):
        features = self._features
        if self.cfg.get('pool_filter') == 'low' and not features.empty:
            # The low-price preference uses prices known on this day, not static metadata.
            eligible = set(features[(features.date.astype(str) == date_str) & (features.close <= 20)].code)
            features = features[features.code.isin(eligible)]
        weight_eval = self._ledger.advance(self._features,self._bench,date_str,self.settings.engine)
        context = replace(self.settings,account=replace(self.settings.account,cash=self._account.cash))
        decision = decide(features,self._bench,date_str,context,weight_eval['new_w_a'],self._account.holdings(),events_cfg=self.events_cfg,
                          allow_new_entries=self.current_idx % int(self.cfg.get("rebalance_interval",5)) == 0)
        return decision

    def _score_and_rank_stocks(self,date_str,prices):
        decision = self._historical_decision(date_str)
        return [dict(code=r.code,name=r['name'],price=r.price,score=r.score_blend,tier='mid')
                for _,r in decision.plan.iterrows()]

    def step(self,days_to_step=1):
        if days_to_step < 0:
            raise ValueError('步进天数不能为负')
        total = len(self.trading_days)
        for _ in range(min(days_to_step,total-self.current_idx)):
            today = self.trading_days[self.current_idx]
            bars = self._bars[self._bars.date.astype(str) == today] if not self._bars.empty else pd.DataFrame()
            if self._pending is not None:
                self._account.execute(self._pending.orders.assign(date=self._pending.date),bars,today,
                                      min(self._pending.macro.max_total_position,self._pending.parameters["risk"]["max_total_position"]),self.settings.account.max_positions)
            self._account.mark(bars)
            self.cash = self._account.cash
            self.positions = self._account.positions
            self.trades = [dict(t,shares=t['filled_shares'],pnl_pct=round(t['pnl']/t['amount']*100,2) if t['amount'] else 0.)
                           for t in self._account.audit if t['filled_shares'] > 0]
            prices = self._get_prices_at(today)
            benchmark = positive_price = risk.positive(prices.get(self.settings.data.index_symbol))
            if self.bench_start_price is None and positive_price:
                self.bench_start_price = benchmark
            bench_return = (benchmark/self.bench_start_price-1)*100 if benchmark and self.bench_start_price else None
            value = self._account.equity
            self.equity_curve.append(dict(date=today,equity=round(value,2),cash=round(self.cash,2),
                return_pct=round((value/self.initial_cash-1)*100,2),
                benchmark_return_pct=round(bench_return,2) if bench_return is not None else None,
                stale_codes=list(self._account.stale_codes)))
            decision = self._historical_decision(today)
            snapshot = decision.to_payload()
            snapshot['benchmark_close'] = benchmark
            self._ledger.freeze(today,snapshot)
            self.decisions.append(snapshot)
            self._pending = self._last_decision = decision
            self.current_idx += 1
        return self.get_state()

    def _calc_total_equity(self,prices):
        return round(self.cash+sum(p['shares']*(risk.positive(prices.get(c)) or p['current_price'])
                                   for c,p in self.positions.items()),2)

    def get_state(self) -> dict[str, Any]:
        """打包输出当前推演沙盒完整状态看板与指标。"""
        total_days = len(self.trading_days)
        curr_date = self.trading_days[self.current_idx - 1] if self.current_idx > 0 else (self.trading_days[0] if total_days > 0 else self.start_date)
        is_finished = self.current_idx >= total_days and total_days > 0

        # 当前总资产与收益
        latest_equity = self.equity_curve[-1]["equity"] if self.equity_curve else self.initial_cash
        metric_reasons = {}
        total_return_pct = round((latest_equity - self.initial_cash) / self.initial_cash * 100, 2) if self.equity_curve else None
        bench_return_pct = self.equity_curve[-1]["benchmark_return_pct"] if self.equity_curve else None
        alpha_pct = round(total_return_pct - bench_return_pct, 2) if total_return_pct is not None and bench_return_pct is not None else None

        # 胜率与交易统计
        sell_trades = [t for t in self.trades if t["side"] == "卖出"]
        win_trades = [t for t in sell_trades if t.get("pnl", 0) > 0]
        win_rate = round(len(win_trades) / len(sell_trades) * 100, 1) if sell_trades else None
        if not sell_trades:
            metric_reasons['win_rate_pct'] = 'no_closed_trades'

        total_gain = sum(t["pnl"] for t in win_trades)
        loss_trades = [t for t in sell_trades if t.get("pnl", 0) < 0]
        total_loss = abs(sum(t["pnl"] for t in loss_trades))
        profit_loss_ratio = round(total_gain / total_loss, 2) if total_loss > 0 else None
        if profit_loss_ratio is None:
            metric_reasons['profit_loss_ratio'] = 'no_losing_trades' if sell_trades else 'no_closed_trades'

        # Performance shares the backtest metric implementation and initial capital.
        max_drawdown_pct = sharpe_ratio = None
        if self.equity_curve:
            from backtest.metrics import compute_metrics
            index = pd.to_datetime([p['date'] for p in self.equity_curve])
            eq = pd.Series([p['equity'] for p in self.equity_curve],index=index)
            beq = pd.Series([self.initial_cash*(1+(p['benchmark_return_pct'] or 0.)/100)
                            for p in self.equity_curve],index=index)
            stats = compute_metrics(eq,beq,initial_cash=self.initial_cash)
            max_drawdown_pct = round(-stats['max_drawdown']*100,2)
            sharpe_ratio = round(stats['sharpe'],2)

        metrics = dict(total_return_pct=total_return_pct, benchmark_return_pct=bench_return_pct,
                       alpha_pct=alpha_pct, max_drawdown_pct=max_drawdown_pct, win_rate_pct=win_rate,
                       profit_loss_ratio=profit_loss_ratio, total_trades=len(sell_trades), sharpe_ratio=sharpe_ratio)
        if not self.equity_curve:
            reason = 'no_trusted_trading_days' if not total_days else 'no_evaluated_trading_days'
            for key in metrics:
                if key != 'total_trades':
                    metrics[key] = None
                    metric_reasons[key] = reason
        elif bench_return_pct is None:
            metric_reasons.update(benchmark_return_pct='missing_benchmark_quote', alpha_pct='missing_benchmark_quote')

        # 格式化当前持仓
        formatted_positions = []
        for code, pos in self.positions.items():
            cost = pos["avg_cost"]
            cur_p = pos.get("current_price", cost)
            val = round(pos["shares"] * cur_p, 2)
            pnl = round(val - pos["shares"] * cost, 2)
            pnl_pct = round((cur_p - cost) / cost * 100, 2)
            formatted_positions.append({
                "code": code,
                "name": pos["name"],
                "shares": pos["shares"],
                "avg_cost": round(cost, 2),
                "current_price": round(cur_p, 2),
                "market_value": val,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "tier": pos.get("tier", "mid"),
            })

        from services.plan_evidence import observed_evidence
        last = self._last_decision.to_payload() if self._last_decision else None
        evidence = observed_evidence(self._features, curr_date,
            list(self._last_decision.plan.code)) if self._last_decision else []
        return {
            "data_info": self.data_info,
            "config": self.cfg,
            "last_decision": last,
            "evidence": evidence,
            "status": "ready" if total_days else "blocked",
            "blocked_reason": None if total_days else "no_trusted_trading_days",
            "metric_reasons": metric_reasons,
            "buying_allowed": self._last_decision.macro.allow_new_buy if self._last_decision else False,
            "risk_reason": ("缺少可信交易日，沙盒无法运行" if not total_days else
                            "" if self._last_decision and self._last_decision.macro.allow_new_buy else "历史时点暂不允许新增买入"),
            "scheme": self.scheme_key,
            "scheme_name": self.cfg.get("name", ""),
            "scheme_desc": self.cfg.get("desc", ""),
            "start_date": self.start_date,
            "end_date": self.end_date,
            "current_date": curr_date,
            "current_step": self.current_idx,
            "total_steps": total_days,
            "progress_pct": round((self.current_idx / total_days * 100), 1) if total_days > 0 else 0,
            "is_finished": is_finished,
            "initial_cash": self.initial_cash,
            "cash": round(self.cash, 2),
            "total_equity": latest_equity,
            "positions": formatted_positions,
            "equity_curve": self.equity_curve,
            "trades": list(reversed(self.trades[-30:])),  # 最近 30 笔明细
            "order_audit": self.order_audit,
            "decision_count": len(self.decisions),
            "evaluation_basis": "冻结预测未来五交易日；成交按次日限价，净值每日估值",
            "metrics": metrics,
        }


# 全局单例沙盒实例
_active_sandbox: HistoricalReplaySandbox | None = None


def get_or_create_sandbox(
    scheme: str = "balanced",
    start_date: str = "2023-01-03",
    end_date: str = "2024-12-31",
    initial_cash: float = 1_000_000.0,
    custom_config: dict[str, Any] | None = None,
    watchlist_codes: list[str] | None = None,
    force_new: bool = False,
    progress=None,
) -> HistoricalReplaySandbox:
    global _active_sandbox
    if _active_sandbox is None or force_new:
        previous = _active_sandbox
        new_sandbox = HistoricalReplaySandbox(
            scheme=scheme,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            custom_config=custom_config,
            watchlist_codes=watchlist_codes,
            progress=progress,
        )
        _active_sandbox = new_sandbox
        if previous is not None:
            previous._ledger.close()
    return _active_sandbox


def run_multi_scheme_comparison(
    start_date: str = "2023-01-03",
    end_date: str = "2024-12-31",
    initial_cash: float = 1_000_000.0,
    custom_config: dict[str, Any] | None = None,
    watchlist_codes: list[str] | None = None,
) -> dict[str, Any]:
    """同时跑满 4 种方案并返回横向对比曲线和综合盈利能力排行榜。"""
    schemes = ["momentum", "value", "balanced"]
    if custom_config:
        schemes.append("custom")

    results = {}
    curves = {}
    metrics_summary = {}

    for s_key in schemes:
        sb = HistoricalReplaySandbox(
            scheme=s_key,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            custom_config=custom_config,
            watchlist_codes=watchlist_codes,
        )
        # 跑满所有周期
        total_days = len(sb.trading_days)
        state = sb.step(days_to_step=total_days)
        results[s_key] = state
        curves[s_key] = [
            {"date": pt["date"], "equity": pt["equity"], "return_pct": pt["return_pct"]}
            for pt in state["equity_curve"]
        ]
        metrics_summary[s_key] = {
            "status": state['status'],
            "blocked_reason": state['blocked_reason'],
            "metric_reasons": state['metric_reasons'],
            "name": state["scheme_name"],
            "total_return_pct": state["metrics"]["total_return_pct"],
            "benchmark_return_pct": state["metrics"]["benchmark_return_pct"],
            "alpha_pct": state["metrics"]["alpha_pct"],
            "max_drawdown_pct": state["metrics"]["max_drawdown_pct"],
            "win_rate_pct": state["metrics"]["win_rate_pct"],
            "sharpe_ratio": state["metrics"]["sharpe_ratio"],
        }

    # 提取基准曲线
    bench_curve = []
    if results and "balanced" in results:
        bench_curve = [
            {"date": pt["date"], "return_pct": pt["benchmark_return_pct"]}
            for pt in results["balanced"]["equity_curve"]
        ]

    return {
        "status": "ready" if any(state['status'] == 'ready' for state in results.values()) else "blocked",
        "blocked_reason": None if any(state['status'] == 'ready' for state in results.values()) else "no_trusted_trading_days",
        "start_date": start_date,
        "end_date": end_date,
        "curves": curves,
        "benchmark_curve": bench_curve,
        "summary": metrics_summary,
    }
