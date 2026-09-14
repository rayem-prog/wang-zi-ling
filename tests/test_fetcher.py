import datetime as dt

import pandas as pd

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
