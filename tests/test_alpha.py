import datetime as dt

import numpy as np
import pandas as pd
import pytest

from features import alpha


def _bars(n=100, seed=7):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    volume = 1000 + rng.integers(-50, 50, n)
    amount = close * volume
    high = close + 0.5
    low = close - 0.5
    return pd.DataFrame(
        {
            "code": ["000001"] * n,
            "date": [dt.date(2023, 1, 1) + dt.timedelta(days=i) for i in range(n)],
            "open": close - 0.2,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "amount": amount,
        }
    )


def test_factor_columns_exist():
    out = alpha.compute_features(_bars())
    for col in alpha.FACTOR_COLUMNS:
        assert col in out.columns


def test_factors_finite_after_warmup():
    out = alpha.compute_features(_bars())
    tail = out.iloc[-20:]
    assert tail[alpha.FACTOR_COLUMNS].notna().all().all()
    assert out.iloc[0]["ret_1"] != out.iloc[0]["ret_1"]  # 前几行为 NaN


def test_requires_warmup_rows():
    with pytest.raises(ValueError):
        alpha.compute_features(_bars(n=60))
