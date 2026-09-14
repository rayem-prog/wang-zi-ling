import datetime as dt

import numpy as np
import pandas as pd
import pytest

from backtest import metrics


def _equity(days=10, step=1.0):
    idx = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(days)]
    return pd.Series(100_000.0 + np.arange(days) * step, index=idx)


def test_benchmark_equity():
    bench = pd.DataFrame(
        {"date": [dt.date(2024, 1, 1), dt.date(2024, 1, 2)], "close": [3000.0, 3030.0]}
    )
    eq = metrics.benchmark_equity(bench, cash=100_000.0)
    assert eq.iloc[0] == 100_000.0
    assert eq.iloc[-1] == 101_000.0


def test_compute_metrics_keys():
    eq = _equity()
    bench = pd.Series(eq.values, index=eq.index)
    m = metrics.compute_metrics(eq, bench)
    for key in ["total_return", "ann_return", "sharpe", "max_drawdown", "win_rate", "excess_total_return"]:
        assert key in m
    assert m["total_return"] == pytest.approx(9 / 100_000, rel=1e-3)


def test_rank_ic_positive_correlation():
    dates = [dt.date(2024, 1, 1)] * 5
    df = pd.DataFrame(
        {
            "date": dates,
            "code": list("abcde"),
            "score": [1.0, 2.0, 3.0, 4.0, 5.0],
            "excess": [0.1, 0.2, 0.3, 0.4, 0.5],
        }
    )
    assert metrics.rank_ic(df) == pytest.approx(1.0)
