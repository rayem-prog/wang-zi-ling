import datetime as dt
import pandas as pd

from app.main import (
    format_pct,
    get_daemon_status_indicator,
    list_recap_reports,
    load_orders_csv,
    load_rec,
)


def test_format_pct():
    assert format_pct(0.123) == "12.30%"


def test_load_rec_missing_returns_empty(tmp_path):
    df = load_rec(str(tmp_path / "nope.csv"))
    assert df.empty


def test_load_orders_csv(tmp_path):
    p = tmp_path / "orders.csv"
    p.write_text("code,name,action\n000001,平安银行,买入", encoding="utf-8")
    df = load_orders_csv(str(p))
    assert len(df) == 1
    assert df.iloc[0]["code"] == "000001"


def test_list_recap_reports(tmp_path):
    rep_dir = tmp_path / "reports"
    rep_dir.mkdir()
    (rep_dir / "2026-09-12.md").write_text("report 1", encoding="utf-8")
    (rep_dir / "2026-09-14.md").write_text("report 2", encoding="utf-8")

    reports = list_recap_reports(str(rep_dir))
    assert reports == ["2026-09-14", "2026-09-12"]


def test_daemon_status_indicator():
    # 1. No heartbeat -> offline
    icon, desc = get_daemon_status_indicator(None)
    assert "离线" in icon

    # 2. Fresh running heartbeat -> green running
    now_utc = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    fresh_hb = {"timestamp": now_utc, "status": "running", "pid": 1234, "message": "all good"}
    icon, desc = get_daemon_status_indicator(fresh_hb)
    assert "运行中" in icon
    assert "1234" in icon

    # 3. Old heartbeat -> disconnected
    old_utc = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=10)).isoformat(timespec="seconds")
    old_hb = {"timestamp": old_utc, "status": "running", "pid": 1234, "message": "old"}
    icon, desc = get_daemon_status_indicator(old_hb)
    assert "已断开" in icon
