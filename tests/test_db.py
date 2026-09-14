import datetime as dt

import pandas as pd

from data import db


def _bars():
    rows = []
    for i in range(3):
        rows.append(
            {
                "code": "000001",
                "date": dt.date(2024, 1, 1 + i),
                "open": 10.0 + i,
                "high": 11.0 + i,
                "low": 9.0 + i,
                "close": 10.5 + i,
                "volume": 1000,
                "amount": 10_000,
            }
        )
    return pd.DataFrame(rows)


def test_bars_roundtrip(tmp_path):
    path = str(tmp_path / "market.db")
    db.init_db(path)
    n = db.save_bars(path, "000001", _bars())
    assert n == 3
    out = db.load_bars(path, "000001")
    assert len(out) == 3
    assert list(out.columns) == ["code", "date", "open", "high", "low", "close", "volume", "amount"]
    assert out["date"].iloc[0] == dt.date(2024, 1, 1)
    assert out["close"].tolist() == [10.5, 11.5, 12.5]


def test_upsert_and_freshness(tmp_path):
    path = str(tmp_path / "market.db")
    db.init_db(path)
    db.save_bars(path, "000001", _bars())
    db.save_bars(path, "000001", _bars())
    assert len(db.load_bars(path, "000001")) == 3
    db.mark_fresh(path, "000001")
    assert db.get_freshness(path, "000001") is not None
    assert db.get_freshness(path, "999999") is None


def test_spot_roundtrip(tmp_path):
    path = str(tmp_path / "market.db")
    db.init_db(path)
    quotes = pd.DataFrame(
        {"code": ["000001"], "name": ["平安银行"], "price": [10.5], "pct_change": [1.2], "volume": [100], "amount": [1050]}
    )
    db.save_spot(path, quotes)
    out = db.load_spot(path)
    assert out.iloc[0]["name"] == "平安银行"
    assert db.list_codes(path) == ["000001"]


def test_minute_bars_roundtrip(tmp_path):
    path = str(tmp_path / "market.db")
    db.init_db(path)
    mbars = pd.DataFrame(
        [
            {"code": "000001", "date": "2026-09-11", "time": "09:30", "open": 10.0, "high": 10.1, "low": 9.9, "close": 10.05, "volume": 500, "amount": 5025},
            {"code": "000001", "date": "2026-09-11", "time": "09:31", "open": 10.05, "high": 10.2, "low": 10.0, "close": 10.15, "volume": 600, "amount": 6090},
            {"code": "000001", "date": "2026-09-12", "time": "09:30", "open": 10.2, "high": 10.3, "low": 10.1, "close": 10.25, "volume": 700, "amount": 7175},
        ]
    )
    n = db.save_minute_bars(path, "000001", mbars)
    assert n == 3
    # Check deduplication
    n2 = db.save_minute_bars(path, "000001", mbars)
    assert n2 == 3

    # Load all
    loaded = db.load_minute_bars(path, "000001")
    assert len(loaded) == 3
    assert list(loaded.columns) == db.MINUTE_BAR_COLUMNS

    # Load with date filter
    d1_bars = db.load_minute_bars(path, "000001", date="2026-09-11")
    assert len(d1_bars) == 2
    assert d1_bars["time"].tolist() == ["09:30", "09:31"]

    # Check latest dates
    assert db.get_latest_minute_date(path, "000001") == "2026-09-12"
    assert db.get_latest_minute_date(path, "999999") is None


def test_get_latest_bar_date(tmp_path):
    path = str(tmp_path / "market.db")
    db.init_db(path)
    assert db.get_latest_bar_date(path, "000001") is None
    db.save_bars(path, "000001", _bars())
    assert db.get_latest_bar_date(path, "000001") == "2024-01-03"


def test_heartbeat(tmp_path):
    path = str(tmp_path / "market.db")
    db.init_db(path)
    assert db.get_latest_heartbeat(path) is None

    db.record_heartbeat(path, status="running", message="all good", pid=12345)
    hb = db.get_latest_heartbeat(path)
    assert hb is not None
    assert hb["status"] == "running"
    assert hb["message"] == "all good"
    assert hb["pid"] == 12345


def test_macro_history(tmp_path):
    path = str(tmp_path / "market.db")
    db.init_db(path)
    db.save_macro_score(
        path,
        date="2026-09-11",
        score=0.45,
        stance="attack",
        trend=0.6,
        breadth=0.4,
        vol=0.2,
        details="Strong uptrend",
    )
    df = db.load_macro_history(path)
    assert len(df) == 1
    assert df.iloc[0]["score"] == 0.45
    assert df.iloc[0]["stance"] == "attack"

