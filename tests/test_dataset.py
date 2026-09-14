import datetime as dt

import pandas as pd

from model import dataset


def _frame(code, base):
    return pd.DataFrame(
        {
            "code": code,
            "date": [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(10)],
            "close": [base + i for i in range(10)],
        }
    )


def test_label_positive_when_stock_outperforms():
    stock = _frame("000001", 100)  # 每天 +1
    dates = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(10)]
    bench = pd.DataFrame({"date": dates, "close": [100 + i * 0.5 for i in range(10)]})  # 每天 +0.5（跑输个股）
    out = dataset.add_future_excess(stock, bench, horizon=5)
    last_valid = out.dropna(subset=["excess_5d"]).iloc[0]
    assert last_valid["label"] == 1
    assert "excess_5d" in out.columns


def test_label_nan_at_history_end():
    stock = _frame("000001", 100)
    dates = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(10)]
    bench = pd.DataFrame({"date": dates, "close": [100 + i * 0.5 for i in range(10)]})
    out = dataset.add_future_excess(stock, bench, horizon=5)
    assert out["label"].tail(5).isna().all()


def test_build_training_data_with_intraday_factors():
    stock = _frame("000001", 100)
    dates = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(10)]
    bench = pd.DataFrame({"date": dates, "close": [100 + i * 0.5 for i in range(10)]})

    # Add dummy daily factors
    from features.alpha import FACTOR_COLUMNS
    for col in FACTOR_COLUMNS:
        stock[col] = 1.0

    # Build with default 18 factors
    X, y, meta = dataset.build_training_data(stock, bench)
    assert X.shape[1] == 18

    # Build with all 28 factors (intraday factors automatically filled with 0.0 if missing)
    X28, y28, meta28 = dataset.build_training_data(stock, bench, feature_cols=dataset.ALL_FACTOR_COLUMNS)
    assert X28.shape[1] == 28
    assert len(X28) == len(X)

