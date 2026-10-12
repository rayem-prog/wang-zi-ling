import datetime as dt

import pandas as pd

from config.settings import Settings
from data import db, pipeline
from data import verified_daily


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
    def fake_capture(code, start, end, adjustment, root):
        frame = (fake_fetch_index(code, start, end) if code == settings.data.index_symbol
                 else fake_fetch_daily(code, start, end, adjustment)).copy()
        frame.attrs.update(source='tencent_newfqkline', adjustment=adjustment,
                           adjustment_version='test-v1', evidence='test://captured-response')
        return frame
    monkeypatch.setattr(verified_daily, 'capture', fake_capture)
    monkeypatch.setattr(pipeline, 'spot_snapshot', lambda *a: pd.DataFrame())
    monkeypatch.setattr(pipeline, 'update_minute_bars', lambda *a, **k: {'updated': []})
    result = pipeline.update_all(settings)
    assert result["updated"] == ["000001"]
    assert result["failed"] == ["999999"]
    assert result["bars_added"] == 4  # 两根股票日线 + 两根指数日线
    assert result["bench_updated"] is True
    assert len(db.load_training_bars(settings.data.db_path, "000001", adjustment="qfq")) == 2
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
    assert fetch_called  # old history is still backfilled even when the latest date is current
    assert result["history_short"] == ["000001"]



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
    assert res["failed"] == []
    assert res["empty"] == ["FAIL01"]
    assert res["bars_added"] == 2
    saved = db.load_minute_bars(settings.data.db_path, "000001")
    assert len(saved) == 2


def test_incremental_update_backfills_history_and_preserves_existing_bar(monkeypatch, tmp_path):
    from dataclasses import replace

    settings = replace(_settings(tmp_path), data=replace(_settings(tmp_path).data,
                       start_date="2021-01-01", end_date="2024-01-03"))
    db.init_db(settings.data.db_path)
    original = pd.DataFrame([{"code": "000001", "date": dt.date(2024, 1, 2),
                              "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
                              "volume": 1000.0, "amount": 10000.0}])
    db.save_bars(settings.data.db_path, "000001", original)
    calls = []

    def fake_fetch(code, start, end, adjust):
        calls.append((start, end))
        if start < "2024-01-01":
            bars = pd.DataFrame([{"code": code, "date": dt.date(2021, 1, 4),
                                  "open": 8.0, "high": 9.0, "low": 7.0, "close": 8.5,
                                  "volume": 900.0, "amount": 8000.0}])
        else:
            bars = pd.DataFrame([{"code": code, "date": dt.date(2024, 1, 3),
                                  "open": 11.0, "high": 12.0, "low": 10.0, "close": 11.5,
                                  "volume": 900.0, "amount": 8000.0}])
        bars.attrs["source"] = "tencent"
        return bars

    monkeypatch.setattr(pipeline.fetcher, "fetch_daily", fake_fetch)
    monkeypatch.setattr(pipeline.fetcher, "fetch_index_daily", lambda *args, **kwargs: pd.DataFrame())
    result = pipeline.update_daily_incremental(settings, ["000001"], sleep_jitter=False)
    assert len(calls) == 2
    assert result["bars_added"] == 2
    loaded = db.load_bars(settings.data.db_path, "000001")
    assert loaded["date"].tolist() == [dt.date(2021, 1, 4), dt.date(2024, 1, 2), dt.date(2024, 1, 3)]
    assert loaded.loc[loaded["date"] == dt.date(2024, 1, 2), "close"].iloc[0] == 10.5
    assert db.get_latest_bar_metadata(settings.data.db_path, "000001")["source"] == "tencent"


def test_current_bars_survive_history_source_failure(monkeypatch, tmp_path):
    from dataclasses import replace
    import sqlite3

    settings = replace(_settings(tmp_path), data=replace(_settings(tmp_path).data,
                       start_date="2021-01-01", end_date="2024-01-03"))
    db.init_db(settings.data.db_path)
    old = pd.DataFrame([{"date": dt.date(2024, 1, 2), "open": 10.0, "high": 10.0,
                         "low": 10.0, "close": 10.0, "volume": 100.0, "amount": 1000.0}])
    db.save_bars(settings.data.db_path, "000001", old)
    calls = []

    def fetch(code, start, end, adjust):
        calls.append(start)
        if start < "2024-01-01":
            raise RuntimeError("provider rate limit")
        return pd.DataFrame([{"date": dt.date(2024, 1, 3), "open": 11.0,
                              "high": 11.0, "low": 11.0, "close": 11.0,
                              "volume": 100.0, "amount": 1100.0}])

    monkeypatch.setattr(pipeline.fetcher, "fetch_daily", fetch)
    monkeypatch.setattr(pipeline.fetcher, "fetch_index_daily", lambda *a, **k: pd.DataFrame())
    result = pipeline.update_daily_incremental(settings, ["000001"], sleep_jitter=False)
    assert calls[0] == "2024-01-03"
    assert "000001" in result["updated"]
    assert "000001" in result["failed"]
    assert db.get_latest_bar_date(settings.data.db_path, "000001") == "2024-01-03"
    with sqlite3.connect(settings.data.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM data_fetch_log WHERE code='000001' AND status='error'").fetchone()[0] == 1


def test_minute_collection_skips_verified_inactive_securities(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    calls = []

    def fetch(code, date=None):
        calls.append(code)
        return pd.DataFrame([{"code": code, "date": date, "time": "09:30",
                              "open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0,
                              "volume": 100.0, "amount": None}])

    monkeypatch.setattr(pipeline.fetcher, "fetch_minute_bars", fetch)
    result = pipeline.update_minute_bars(
        settings,
        ["000001", "600837", "601059", "601238", "601989"],
        date="2026-09-24",
        sleep_jitter=False,
    )

    assert calls == ["000001"]
    assert result["updated"] == ["000001"]
    assert {row["code"] for row in result["excluded_inactive_codes"]} == {
        "600837", "601059", "601238", "601989"
    }
