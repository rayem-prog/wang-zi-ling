import pandas as pd
import pytest

from config.settings import Settings
from strategy import recap
from strategy.macro import MacroResult


def test_generate_orders_stop_loss_and_take_profit():
    s = Settings()
    # Holding 1: cost 10.0, current 9.4 (-6% -> triggers -5% stop loss)
    # Holding 2: cost 10.0, current 11.6 (+16% -> triggers +15% take profit)
    holdings = pd.DataFrame(
        [
            {"code": "000001", "name": "平安银行", "shares": 1000, "cost_price": 10.0, "price": 9.4},
            {"code": "000002", "name": "万科A", "shares": 500, "cost_price": 10.0, "price": 11.6},
        ]
    )
    orders = recap.generate_daily_orders(recommendations=None, current_holdings=holdings, settings=s)
    assert len(orders) == 2
    acts = dict(zip(orders["code"], orders["action"]))
    assert acts["000001"] == "清仓"
    assert acts["000002"] == "止盈卖出"


def test_generate_orders_defense_blocks_buy():
    s = Settings(account=Settings().account.__class__(cash=100_000.0))
    recs = pd.DataFrame(
        [
            {
                "code": "600519",
                "name": "贵州茅台",
                "price": 1000.0,
                "score_blend": 88.0,
                "suggested_weight": 0.2,
                "suggested_amount": 20000.0,
            }
        ]
    )
    defense_macro = MacroResult(
        score=-0.5,
        stance="defense",
        trend_score=-0.5,
        breadth_score=-0.5,
        vol_score=-0.5,
        max_total_position=0.30,
        allow_new_buy=False,
        event_penalty_applied=False,
    )
    orders = recap.generate_daily_orders(
        recommendations=recs,
        current_holdings=None,
        settings=s,
        macro_res=defense_macro,
    )
    # Defense blocks all new buy orders
    assert len(orders) == 0


def test_recap_and_summary_output(tmp_path):
    report_dir = str(tmp_path / "reports")
    orders = pd.DataFrame(
        [
            {
                "code": "000001",
                "name": "平安银行",
                "action": "买入",
                "shares": 1000,
                "price_ref": 10.0,
                "limit_low": 9.95,
                "limit_high": 10.05,
                "amount": 10000.0,
                "weight": 0.1,
                "stop_loss": 9.5,
                "take_profit": 11.5,
                "reason": "综合分 80",
                "warning": "涨停不追",
            }
        ]
    )
    macro_res = MacroResult(
        score=0.4,
        stance="attack",
        trend_score=0.5,
        breadth_score=0.3,
        vol_score=0.2,
        max_total_position=0.80,
        allow_new_buy=True,
        event_penalty_applied=False,
    )

    md = recap.generate_daily_recap(
        trade_date="2026-09-14",
        market_summary={"index_text": "沪深300 +0.5%"},
        holdings_summary={"market_value": 50000.0, "daily_pnl": 500.0, "daily_pnl_pct": 1.0, "excess_pct": 0.5},
        engine_perf={"engine_a": "+0.6%", "engine_b": "+0.4%", "blend": "+0.5%"},
        orders_df=orders,
        macro_res=macro_res,
        save_dir=report_dir,
    )
    assert "# StockPilot 每日复盘与决策简报" in md
    assert "000001" in md
    assert (tmp_path / "reports" / "2026-09-14.md").exists()

    summary = recap.generate_notification_summary("2026-09-14", macro_res=macro_res, orders_df=orders)
    assert "ATTACK" in summary
    assert "000001" in summary
