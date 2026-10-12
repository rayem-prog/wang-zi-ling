import datetime as dt
from dataclasses import replace

import pandas as pd

from api.routers import kline
from config.settings import Settings
from data import db, fetcher


def _settings(tmp_path):
    s = replace(Settings(), data=replace(Settings().data, db_path=str(tmp_path / "market.db")),
                paths=replace(Settings().paths, artifact_dir=str(tmp_path / "artifacts")))
    db.init_db(s.data.db_path)
    return s


def test_kline_never_fabricates_missing_daily_or_minute_bars(monkeypatch, tmp_path):
    s = _settings(tmp_path)
    monkeypatch.setattr(kline, "load_settings", lambda: s)
    monkeypatch.setattr(kline.fetcher, "fetch_daily", lambda *a, **kw: pd.DataFrame())
    monkeypatch.setattr(kline.fetcher, "fetch_minute_bars", lambda *a, **kw: pd.DataFrame())
    daily = kline.get_daily_kline("000001")
    minute = kline.get_minute_kline("000001")
    for result in (daily, minute):
        assert result["available"] is False
        assert result["dates"] == []
        assert result["values"] == []
        assert result["message"]


def test_fetched_real_daily_bars_are_cached_with_provenance(monkeypatch, tmp_path):
    s = _settings(tmp_path)
    monkeypatch.setattr(kline, "load_settings", lambda: s)
    calls = []

    def fetch(*args, **kwargs):
        calls.append(args)
        bars = pd.DataFrame([{"code": "000001", "date": dt.date(2026, 9, 25),
                              "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
                              "volume": 1000.0, "amount": 10000.0}])
        bars.attrs["source"] = "tencent"
        return bars

    monkeypatch.setattr(kline.fetcher, "fetch_daily", fetch)
    result = kline.get_daily_kline("000001")
    assert result["available"] is True
    assert result["source"] == "tencent"
    assert result["trade_date"] == "2026-09-25"
    assert len(db.load_bars(s.data.db_path, "000001")) == 1
    assert len(calls) == 1


def test_tencent_provider_minute_ohlc_is_persisted_without_fake_amount(monkeypatch, tmp_path):
    s = _settings(tmp_path)
    payload = {
        "code": 0,
        "data": {"sh600000": {"m1": [
            ["202609280930", "9.03", "9.03", "9.03", "9.03", "2750.00", {}, "0.08"],
            ["202609280931", "9.02", "9.05", "9.06", "9.01", "21977.00", {}, "0.66"],
        ]}}
    }

    class Response:
        text = "m1_today=" + __import__("json").dumps(payload)

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setattr(fetcher._SESSION, "get", lambda *args, **kwargs: Response())
    bars = fetcher.fetch_minute_bars("600000", date="2026-09-28")
    assert bars.attrs["source"] == "tencent_mkline"
    assert len(bars) == 2
    assert db.save_minute_bars(s.data.db_path, "600000", bars, source=bars.attrs["source"]) == 2
    stored = db.load_minute_bars(s.data.db_path, "600000", date="2026-09-28")
    assert stored["high"].tolist() == [9.03, 9.06]
    assert stored["low"].tolist() == [9.03, 9.01]
    assert stored["amount"].isna().all()


def test_kline_explains_provider_failure_without_bars(monkeypatch, tmp_path):
    s = _settings(tmp_path)
    monkeypatch.setattr(kline, "load_settings", lambda: s)
    def fail(*args, **kwargs):
        raise RuntimeError("provider refused request")
    monkeypatch.setattr(kline.fetcher, "fetch_daily", fail)
    result = kline.get_daily_kline("000001")
    assert result["available"] is False
    assert "provider refused request" in result["message"]
    assert result["values"] == []
