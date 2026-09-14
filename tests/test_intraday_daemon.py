import datetime as dt
import pandas as pd
import pytest

from config.settings import Settings
from data import db
from scripts import intraday_daemon
from strategy.notify import MockNotifier, CompositeNotifier


def test_is_trading_time():
    # Monday 09:35 -> True
    mon_morning = dt.datetime(2026, 9, 14, 9, 35)
    assert intraday_daemon.is_trading_time(mon_morning) is True

    # Monday 12:00 (lunch break) -> False
    mon_lunch = dt.datetime(2026, 9, 14, 12, 0)
    assert intraday_daemon.is_trading_time(mon_lunch) is False

    # Monday 14:30 -> True
    mon_afternoon = dt.datetime(2026, 9, 14, 14, 30)
    assert intraday_daemon.is_trading_time(mon_afternoon) is True

    # Monday 15:30 -> False
    mon_after_close = dt.datetime(2026, 9, 14, 15, 30)
    assert intraday_daemon.is_trading_time(mon_after_close) is False

    # Saturday 10:00 -> False
    sat = dt.datetime(2026, 9, 19, 10, 0)
    assert intraday_daemon.is_trading_time(sat) is False


def test_check_intraday_triggers():
    mock = MockNotifier()
    notifier = CompositeNotifier(mock_notifier=mock, mode="mock")
    settings = Settings()

    # Holdings:
    # 000001: cost 10.0, current price 9.4 -> -6% loss (triggers P0 stop loss at -5%)
    # 000002: cost 10.0, current price 11.6 -> +16% gain (triggers P0 take profit at +15%)
    holdings = pd.DataFrame(
        [
            {"code": "000001", "name": "平安银行", "shares": 1000, "cost_price": 10.0},
            {"code": "000002", "name": "万科A", "shares": 1000, "cost_price": 10.0},
        ]
    )

    # Orders:
    # 600519: Buy order, limit range [99.0, 101.0], current 100.0 -> triggers P1 in_range
    # 000063: Buy order, but pct_change +9.9% -> triggers P1 limit_up abort
    orders = pd.DataFrame(
        [
            {"code": "600519", "name": "贵州茅台", "action": "买入", "limit_low": 99.0, "limit_high": 101.0},
            {"code": "000063", "name": "中兴通讯", "action": "买入", "limit_low": 20.0, "limit_high": 21.0},
        ]
    )

    spot = pd.DataFrame(
        [
            {"code": "000001", "name": "平安银行", "price": 9.4, "pct_change": -3.0},
            {"code": "000002", "name": "万科A", "price": 11.6, "pct_change": 4.0},
            {"code": "600519", "name": "贵州茅台", "price": 100.0, "pct_change": 1.0},
            {"code": "000063", "name": "中兴通讯", "price": 22.0, "pct_change": 9.9},
        ]
    )

    triggers = intraday_daemon.check_intraday_triggers(
        spot_df=spot,
        orders_df=orders,
        holdings_df=holdings,
        settings=settings,
        notifier=notifier,
    )

    assert len(triggers) == 4
    # Check that 2 P0 alerts and 2 P1 alerts were dispatched
    p0_msgs = [m for m in mock.sent_messages if m["level"] == "P0"]
    p1_msgs = [m for m in mock.sent_messages if m["level"] == "P1"]
    assert len(p0_msgs) == 2
    assert len(p1_msgs) == 2


def test_daemon_runs_once(monkeypatch, tmp_path):
    db_path = str(tmp_path / "market.db")
    s = Settings(
        data=Settings().data.__class__(db_path=db_path),
        notify=Settings().notify.__class__(backend="mock"),
    )
    fake_spot = pd.DataFrame(
        [{"code": "000001", "name": "平安银行", "price": 10.0, "pct_change": 1.0, "volume": 100, "amount": 1000}]
    )
    monkeypatch.setattr(intraday_daemon.fetcher, "fetch_spot_batch", lambda codes: fake_spot)

    intraday_daemon.run_daemon(settings=s, once=True)

    hb = db.get_latest_heartbeat(db_path)
    assert hb is not None
    assert hb["status"] in ("stopped", "running")
