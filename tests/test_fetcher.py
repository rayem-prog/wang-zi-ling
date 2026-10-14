import datetime as dt

import pandas as pd
import pytest

from data import fetcher


class FakeAk:
    @staticmethod
    def stock_zh_a_hist(symbol, period, start_date, end_date, adjust):
        return pd.DataFrame(
            {
                "日期": ["2024-01-02", "2024-01-03"],
                "开盘": [10.0, 10.5],
                "收盘": [10.4, 10.8],
                "最高": [10.6, 10.9],
                "最低": [9.9, 10.4],
                "成交量": [1000, 1200],
                "成交额": [10_400, 12_800],
            }
        )

    @staticmethod
    def stock_zh_a_spot_em():
        return pd.DataFrame(
            {
                "代码": ["000001", "600000"],
                "名称": ["平安银行", "浦发银行"],
                "最新价": [10.5, 7.2],
                "涨跌幅": [1.2, -0.5],
                "成交量": [100, 200],
                "成交额": [1050, 1440],
            }
        )

    @staticmethod
    def stock_zh_index_daily_em(symbol):
        return pd.DataFrame(
            {
                "date": ["2024-01-02", "2024-01-03"],
                "open": [3000.0, 3010.0],
                "close": [3010.0, 3005.0],
                "high": [3020.0, 3020.0],
                "low": [2990.0, 3000.0],
                "volume": [1e8, 1.1e8],
                "amount": [3e10, 3.3e10],
            }
        )

    @staticmethod
    def index_stock_cons_csindex(symbol):
        return pd.DataFrame({"成分券代码": ["1", "2"], "成分券名称": ["A", "B"]})


def test_fetch_daily_normalizes(monkeypatch):
    monkeypatch.setattr(fetcher, "_ak", FakeAk)
    out = fetcher.fetch_daily("000001", start="2024-01-01")
    assert list(out.columns) == ["code", "date", "open", "high", "low", "close", "volume", "amount"]
    assert out["code"].tolist() == ["000001", "000001"]
    assert out["date"].iloc[0] == dt.date(2024, 1, 2)
    assert out["close"].tolist() == [10.4, 10.8]


def test_fetch_spot_filters(monkeypatch):
    monkeypatch.setattr(fetcher, "_ak", FakeAk)
    out = fetcher.fetch_spot_batch(["000001"])
    assert len(out) == 1
    assert out.iloc[0]["name"] == "平安银行"


def test_fetch_universe_pads_codes(monkeypatch):
    monkeypatch.setattr(fetcher, "_ak", FakeAk)
    out = fetcher.fetch_universe(extra_codes=("3",))
    assert "000001" in out and "000002" in out and "000003" in out


def test_tencent_symbol_mapping():
    assert fetcher._tencent_symbol("600000") == "sh600000"
    assert fetcher._tencent_symbol("000001") == "sz000001"
    assert fetcher._tencent_symbol("300750") == "sz300750"


def test_rows_to_bars_filters_by_range():
    rows = [
        ["2024-01-02", "10", "10.5", "11", "9.9", "1000"],
        ["2024-02-01", "10.5", "11", "11.5", "10", "2000"],
        ["2024-03-01", "11", "11.5", "12", "10.8", "3000"],
    ]
    out = fetcher._rows_to_bars("600000", rows, start="2024-01-15", end="2024-02-15")
    assert len(out) == 1
    assert out.iloc[0]["date"] == __import__("datetime").date(2024, 2, 1)


def test_long_range_tencent_fetch_is_paginated_and_tracks_source(monkeypatch):
    calls = []

    def fake_tencent(code, start, end, adjust):
        calls.append((start, end))
        day = dt.date.fromisoformat(start)
        return pd.DataFrame([{"code": code, "date": day, "open": 10.0, "high": 10.0,
                              "low": 10.0, "close": 10.0, "volume": 100.0, "amount": 1000.0}])

    monkeypatch.setattr(fetcher, "_PREFER", "tx")
    monkeypatch.setattr(fetcher, "_fetch_daily_tencent", fake_tencent)
    monkeypatch.setattr(fetcher, "_fetch_daily_em", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected fallback")))
    out = fetcher.fetch_daily("000001", "2021-01-01", "2024-12-31")
    assert len(calls) >= 4
    assert len(out) == len(calls)
    assert out.attrs["source"] == "tencent"


def test_minute_fetch_reports_all_provider_failures(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("provider unavailable")
    monkeypatch.setattr(fetcher, "_fetch_minute_tencent_mkline", fail, raising=False)
    monkeypatch.setattr(fetcher, "_fetch_minute_sina", fail)
    monkeypatch.setattr(fetcher, "_fetch_minute_em", fail)
    with pytest.raises(RuntimeError, match="分钟线采集失败"):
        fetcher.fetch_minute_bars("000001")


def test_tencent_mkline_parses_provider_ohlc_without_inventing_amount(monkeypatch):
    import json

    payload = {
        "code": 0,
        "data": {"sh600000": {"m1": [
            ["202609280930", "9.03", "9.03", "9.03", "9.03", "2750.00", {}, "0.08"],
            ["202609280931", "9.02", "9.05", "9.06", "9.01", "21977.00", {}, "0.66"],
        ]}}
    }

    class Response:
        text = "m1_today=" + json.dumps(payload)

        @staticmethod
        def raise_for_status():
            return None

    calls = []

    def fake_get(url, params, timeout):
        calls.append((url, params))
        return Response()

    monkeypatch.setattr(fetcher._SESSION, "get", fake_get)
    out = fetcher._fetch_minute_tencent_mkline("600000", count=240)

    assert calls[0][0] == "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
    assert out["time"].tolist() == ["09:30", "09:31"]
    assert out["open"].tolist() == [9.03, 9.02]
    assert out["close"].tolist() == [9.03, 9.05]
    assert out["high"].tolist() == [9.03, 9.06]
    assert out["low"].tolist() == [9.03, 9.01]
    assert pd.isna(out["amount"]).all()
    assert out.attrs["source"] == "tencent_mkline"


def test_missing_year_inside_history_cannot_be_reported_as_complete(monkeypatch):
    def primary(code, start, end, adjust):
        if start.startswith("2022"):
            return pd.DataFrame()
        return pd.DataFrame([{"code": code, "date": dt.date.fromisoformat(start),
                              "open": 10.0, "high": 10.0, "low": 10.0,
                              "close": 10.0, "volume": 100.0, "amount": 1000.0}])
    monkeypatch.setattr(fetcher, "_PREFER", "tx")
    monkeypatch.setattr(fetcher, "_fetch_daily_tencent", primary)
    monkeypatch.setattr(fetcher, "_fetch_daily_em", lambda *a, **k: pd.DataFrame())
    with pytest.raises(RuntimeError, match="历史区间无数据"):
        fetcher.fetch_daily("000001", "2021-01-01", "2023-12-31")


def test_final_partial_exchange_holiday_does_not_discard_valid_history(monkeypatch):
    def primary(code, start, end, adjust):
        if start.startswith("2023"):
            return pd.DataFrame()
        return pd.DataFrame([{"code": code, "date": dt.date.fromisoformat(start),
                              "open": 10.0, "high": 10.0, "low": 10.0,
                              "close": 10.0, "volume": 100.0, "amount": 1000.0}])

    monkeypatch.setattr(fetcher, "_PREFER", "tx")
    monkeypatch.setattr(fetcher, "_fetch_daily_tencent", primary)
    monkeypatch.setattr(fetcher, "_fetch_daily_em", lambda *a, **k: pd.DataFrame())
    out = fetcher.fetch_daily("000001", "2021-01-01", "2023-01-02")
    assert len(out) == 2


def test_empty_base_universe_cannot_be_hidden_by_extra_codes(monkeypatch):
    monkeypatch.setattr(fetcher, '_fetch_universe_sina', lambda *args: [])
    monkeypatch.setattr(fetcher, '_fetch_universe_ak', lambda *args: [])
    with pytest.raises(RuntimeError, match='基础证券池为空'):
        fetcher.fetch_universe(base_pool='hs300', extra_codes=('000002',))
    assert fetcher.fetch_universe(base_pool='', extra_codes=('000002',)) == ['000002']
