import datetime as dt
import json
import sqlite3
from dataclasses import replace
import pandas as pd
import pytest

from config.settings import Settings
from data import db
from data.health import _expected_session
from scripts import intraday_daemon
from strategy import holdings
from strategy.notify import MockNotifier, CompositeNotifier


@pytest.fixture(autouse=True)
def isolated_daemon_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(intraday_daemon, "build_notifier",
                        lambda _: CompositeNotifier(mock_notifier=MockNotifier(), mode="mock"))


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
    mid_autumn = dt.datetime(2026, 9, 25, 10, 0)
    assert intraday_daemon.is_trading_time(mid_autumn) is False


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


def test_daemon_monitors_positions_watchlist_and_orders_end_to_end(monkeypatch, tmp_path):
    # Closed-session quotes are valid on Sunday, but stale on Monday morning.
    # Keep this scenario independent of the machine's current trading session.
    fixed_now = dt.datetime(2026, 10, 11, 16, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
    from data import health
    monkeypatch.setattr(health, "exchange_now", lambda *_: fixed_now)
    monkeypatch.setattr(intraday_daemon, "exchange_now", lambda *_: fixed_now)
    s = replace(
        Settings(),
        data=replace(Settings().data, db_path=str(tmp_path / "market.db")),
        paths=replace(Settings().paths, artifact_dir=str(tmp_path / "artifacts")),
        notify=replace(Settings().notify, backend="mock"),
    )
    db.init_db(s.data.db_path)
    holdings.save_positions(s.data.db_path, pd.DataFrame([
        {"code": "000001", "name": "止损股", "shares": 100, "cost": 10.0},
        {"code": "000002", "name": "止盈股", "shares": 100, "cost": 10.0},
    ]))
    with sqlite3.connect(s.data.db_path) as conn:
        conn.execute("CREATE TABLE user_watchlist (code TEXT PRIMARY KEY, name TEXT, added_at TEXT)")
        conn.execute("INSERT INTO user_watchlist VALUES ('000003', '自选股', '2026-09-14')")
    orders_path = tmp_path / "artifacts" / "latest_orders.csv"
    orders_path.parent.mkdir()
    pd.DataFrame([{"code": "000004", "name": "指令股", "action": "买入", "limit_low": 9.5, "limit_high": 10.5}]).to_csv(orders_path, index=False)
    session = _expected_session(fixed_now)
    from data.trading_calendar import expected_sessions
    dates = expected_sessions("2021-01-04", session.isoformat())
    for code in ("sh000300", "000001", "000002", "000003", "000004"):
        db.publish_bar_snapshot(s.data.db_path, code, pd.DataFrame([{
            "code": code, "date": day, "open": 10.0, "high": 10.0,
            "low": 10.0, "close": 10.0, "volume": 100, "amount": 1000,
        } for day in dates]), source="test", adjustment="none" if code=="sh000300" else "qfq",
            adjustment_version="v1", evidence="test://daemon-fixture", reason="controlled fixture")
    (orders_path.parent / "recommendations.csv").write_text("code,price\n000004,10\n")
    (orders_path.parent / "latest_recommendations.meta.json").write_text(json.dumps({
        "trade_date": session.isoformat(), "source": "test",
        "generated_at": dt.datetime.now().isoformat(), "coverage": 1.0,
    }))
    seen = []

    def quotes(codes):
        seen.extend(codes)
        rows = [
            {"code": "000001", "name": "止损股", "price": 9.4, "pct_change": -6.0},
            {"code": "000002", "name": "止盈股", "price": 11.6, "pct_change": 16.0},
            {"code": "000003", "name": "自选股", "price": 10.0, "pct_change": 0.0},
            {"code": "000004", "name": "指令股", "price": 10.0, "pct_change": 0.0},
        ]
        return pd.DataFrame([{**row, "volume": 1000, "amount": 10000,
                              "trade_date": session.isoformat(),
                              "quote_time": session.strftime("%Y%m%d") + "150000"} for row in rows])

    mock = MockNotifier()
    notifier = CompositeNotifier(mock_notifier=mock, mode="mock")
    monkeypatch.setattr(intraday_daemon.fetcher, "fetch_spot_batch", quotes)
    monkeypatch.setattr(intraday_daemon, "build_notifier", lambda _: notifier)
    monkeypatch.setattr(intraday_daemon, "is_trading_time", lambda *_: False)
    intraday_daemon.run_daemon(settings=s, once=True)
    assert set(seen) == {"000001", "000002", "000003", "000004"}
    assert len([m for m in mock.sent_messages if m["level"] == "P0"]) == 2
    assert len([m for m in mock.sent_messages if m["level"] == "P1"]) == 1
    with sqlite3.connect(s.data.db_path) as conn:
        messages = conn.execute("SELECT status,message FROM daemon_heartbeat").fetchall()
    assert any(status == "running" and "触发 3 项" in message for status, message in messages)


def test_daemon_records_input_failure_instead_of_running(monkeypatch, tmp_path):
    s = replace(Settings(), data=replace(Settings().data, db_path=str(tmp_path / "market.db")))
    db.init_db(s.data.db_path)
    monkeypatch.setattr(holdings, "load_positions", lambda _: (_ for _ in ()).throw(OSError("持仓损坏")))
    monkeypatch.setattr(intraday_daemon, "build_notifier", lambda _: CompositeNotifier(mock_notifier=MockNotifier(), mode="mock"))
    monkeypatch.setattr(intraday_daemon.fetcher, "fetch_spot_batch", lambda _: pd.DataFrame())
    intraday_daemon.run_daemon(settings=s, once=True)
    with sqlite3.connect(s.data.db_path) as conn:
        heartbeats = conn.execute("SELECT status,message FROM daemon_heartbeat").fetchall()
    assert any(status == "error" and "持仓损坏" in message for status, message in heartbeats)
    assert not any(status == "running" for status, _ in heartbeats)


def test_daemon_does_not_report_running_when_quotes_are_empty(monkeypatch, tmp_path):
    s = replace(Settings(), data=replace(Settings().data, db_path=str(tmp_path / "market.db")),
                notify=replace(Settings().notify, backend="mock"),
                universe=replace(Settings().universe, extra_codes=("000001",)))
    monkeypatch.setattr(intraday_daemon.fetcher, "fetch_spot_batch", lambda _: pd.DataFrame())
    intraday_daemon.run_daemon(settings=s, once=True)
    with sqlite3.connect(s.data.db_path) as conn:
        heartbeats = conn.execute("SELECT status,message FROM daemon_heartbeat").fetchall()
    assert any(status == "error" and "行情为空" in message for status, message in heartbeats)
    assert not any(status == "running" for status, _ in heartbeats)


def test_stale_quote_cannot_trigger_a_stop_loss(monkeypatch, tmp_path):
    s = replace(Settings(), data=replace(Settings().data, db_path=str(tmp_path / "market.db")),
                notify=replace(Settings().notify, backend="mock"))
    db.init_db(s.data.db_path)
    holdings.save_positions(s.data.db_path, pd.DataFrame([
        {"code": "000001", "name": "持仓", "shares": 100, "cost": 10.0}
    ]))
    mock = MockNotifier()
    monkeypatch.setattr(intraday_daemon, "build_notifier", lambda _: CompositeNotifier(mock_notifier=mock, mode="mock"))
    monkeypatch.setattr(intraday_daemon.fetcher, "fetch_spot_batch", lambda _: pd.DataFrame([
        {"code": "000001", "name": "持仓", "price": 8.0, "pct_change": -5.0,
         "volume": 100, "amount": 800, "trade_date": "2000-01-01"}
    ]))
    intraday_daemon.run_daemon(settings=s, once=True)
    assert mock.sent_messages == []
    with sqlite3.connect(s.data.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM daemon_heartbeat WHERE status='error' AND message LIKE '%过期价格%'").fetchone()[0] == 1
