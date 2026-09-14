import datetime as dt

import pandas as pd

from config.settings import Settings
from data import db, pipeline


def _settings(tmp_path):
    return Settings(data=Settings().data.__class__(db_path=str(tmp_path / "market.db")))


def test_update_all(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    db.init_db(settings.data.db_path)
    bars = pd.DataFrame(
        {
            "code": "000001",
            "date": [dt.date(2024, 1, 2), dt.date(2024, 1, 3)],
            "open": [10.0, 10.5], "high": [10.6, 10.9],
            "low": [9.9, 10.4], "close": [10.4, 10.8],
            "volume": [1000, 1200], "amount": [10400, 12800],
        }
    )

    def fake_fetch_daily(code, start, end=None, adjust="qfq"):
        if code == "999999":
            raise RuntimeError("network down")
        return bars

    def fake_fetch_index(symbol, start=None, end=None):
        return pd.DataFrame(
            {
                "code": "sh000300",
                "date": bars["date"],
                "open": [3000.0] * 2, "high": [3010.0] * 2,
                "low": [2990.0] * 2, "close": [3005.0, 3006.0],
                "volume": [1e8] * 2, "amount": [3e10] * 2,
            }
        )

    monkeypatch.setattr(pipeline, "fetch_universe", lambda settings: ["000001", "999999"])
    monkeypatch.setattr(pipeline.fetcher, "fetch_daily", fake_fetch_daily)
    monkeypatch.setattr(pipeline.fetcher, "fetch_index_daily", fake_fetch_index)
    result = pipeline.update_all(settings)
    assert result["updated"] == ["000001"]
    assert result["failed"] == ["999999"]
    assert result["bars_added"] == 2
    assert result["bench_updated"] is True
    assert db.get_freshness(settings.data.db_path, "000001") is not None


def test_spot_snapshot(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    db.init_db(settings.data.db_path)
    quotes = pd.DataFrame(
        {"code": ["000001"], "name": ["平安银行"], "price": [10.5], "pct_change": [1.0], "volume": [100], "amount": [1050]}
    )
    monkeypatch.setattr(pipeline.fetcher, "fetch_spot_batch", lambda codes: quotes)
    out = pipeline.spot_snapshot(settings, ["000001"])
    assert out.iloc[0]["name"] == "平安银行"
    assert len(db.load_spot(settings.data.db_path)) == 1


def test_incremental_skip(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    db.init_db(settings.data.db_path)

    # Save initial bar up to 2026-09-14
    bars = pd.DataFrame(
        {
            "code": "000001",
            "date": [dt.date(2026, 9, 14)],
            "open": [10.0], "high": [10.5], "low": [9.9], "close": [10.2],
            "volume": [1000], "amount": [10200],
        }
    )
    db.save_bars(settings.data.db_path, "000001", bars)

    fetch_called = False

    def fake_fetch_daily(code, start, end=None, adjust="qfq"):
        nonlocal fetch_called
        fetch_called = True
        return pd.DataFrame()

    monkeypatch.setattr(pipeline.fetcher, "fetch_daily", fake_fetch_daily)
    monkeypatch.setattr(pipeline.fetcher, "fetch_index_daily", lambda *args, **kwargs: pd.DataFrame())

    import dataclasses

    settings = dataclasses.replace(
        settings,
        data=dataclasses.replace(settings.data, end_date="2026-09-14")
    )
    result = pipeline.update_daily_incremental(settings, ["000001"], sleep_jitter=False)
    assert result["skipped"] == ["000001"]
    assert not fetch_called



def test_update_minute_bars(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    db.init_db(settings.data.db_path)

    mbars = pd.DataFrame(
        [
            {"code": "000001", "date": "2026-09-14", "time": "09:30", "open": 10.0, "high": 10.1, "low": 9.9, "close": 10.05, "volume": 500, "amount": 5025},
            {"code": "000001", "date": "2026-09-14", "time": "09:31", "open": 10.05, "high": 10.2, "low": 10.0, "close": 10.15, "volume": 600, "amount": 6090},
        ]
    )

    def fake_fetch_minute(code, date=None):
        if code == "FAIL01":
            return pd.DataFrame(columns=db.MINUTE_BAR_COLUMNS)
        return mbars

    monkeypatch.setattr(pipeline.fetcher, "fetch_minute_bars", fake_fetch_minute)
    res = pipeline.update_minute_bars(settings, ["000001", "FAIL01"], sleep_jitter=False)
    assert res["updated"] == ["000001"]
    assert res["failed"] == ["FAIL01"]
    assert res["bars_added"] == 2
    saved = db.load_minute_bars(settings.data.db_path, "000001")
    assert len(saved) == 2

