import numpy as np
import pandas as pd
import pytest

from features import intraday


def test_empty_and_single_bar():
    empty_df = pd.DataFrame(columns=["code", "date", "time", "open", "high", "low", "close", "volume", "amount"])
    out = intraday.compute_intraday_features(empty_df)
    assert list(out.columns) == ["code", "date"] + intraday.INTRADAY_FACTOR_COLUMNS
    assert len(out) == 0

    single_bar = pd.DataFrame(
        [
            {
                "code": "000001",
                "date": "2026-09-14",
                "time": "09:30",
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "volume": 100,
                "amount": 1000,
            }
        ]
    )
    res = intraday.compute_intraday_features(single_bar)
    assert len(res) == 1
    # All 10 factor columns should be 0.0 for a single bar
    for col in intraday.INTRADAY_FACTOR_COLUMNS:
        assert res[col].iloc[0] == 0.0


def test_synthetic_intraday_factors():
    # 4 bars across the day: 09:30, 10:00, 14:30, 15:00
    # Day Open: 10.0
    # 09:30: open=10.0, close=10.2, vol=100
    # 10:00: open=10.2, close=10.5, vol=100 (open30 close = 10.5, ret = 10.5/10.0 - 1 = +5%)
    # 14:30: open=10.4, close=10.6, vol=50 (late open = 10.4)
    # 15:00: open=10.6, close=11.0, vol=50 (late close = 11.0, ret = 11.0/10.4 - 1 = +5.769%)
    # Day Close: 11.0 -> intraday_mom = 11.0/10.0 - 1 = +10%
    rows = [
        {"code": "000001", "date": "2026-09-14", "time": "09:30", "open": 10.0, "high": 10.2, "low": 9.9, "close": 10.2, "volume": 100.0, "amount": 1020.0},
        {"code": "000001", "date": "2026-09-14", "time": "10:00", "open": 10.2, "high": 10.5, "low": 10.1, "close": 10.5, "volume": 100.0, "amount": 1050.0},
        {"code": "000001", "date": "2026-09-14", "time": "14:30", "open": 10.4, "high": 10.6, "low": 10.3, "close": 10.6, "volume": 50.0, "amount": 530.0},
        {"code": "000001", "date": "2026-09-14", "time": "15:00", "open": 10.6, "high": 11.0, "low": 10.5, "close": 11.0, "volume": 50.0, "amount": 550.0},
    ]
    df = pd.DataFrame(rows)
    res = intraday.compute_intraday_features(df)
    assert len(res) == 1
    row = res.iloc[0]

    # Check open30_ret
    assert pytest.approx(row["open30_ret"], 0.001) == 0.05

    # Check intraday_mom
    assert pytest.approx(row["intraday_mom"], 0.001) == 0.10

    # Total vol = 300, Morning vol (<=10:30) = 200 -> share = 200/300 = 2/3
    assert pytest.approx(row["morning_vol_share"], 0.001) == 2 / 3

    # Late vol (>=14:00) = 100 -> share = 100/300 = 1/3
    assert pytest.approx(row["late_vol_share"], 0.001) == 1 / 3

    # Trend R^2: prices 10.2, 10.5, 10.6, 11.0 are strictly increasing -> R^2 should be close to 1
    assert row["minute_trend_r2"] > 0.9
