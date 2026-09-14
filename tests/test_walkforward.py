import numpy as np
import pandas as pd

from backtest.walkforward import BacktestConfig, run_walk_forward


def _dataset(codes=("000001", "000002", "000003", "000004"), days=400):
    rng = np.random.default_rng(3)
    frames = []
    for i, code in enumerate(codes):
        dates = pd.bdate_range("2023-01-02", periods=days)
        close = 100 + i * 10 + np.cumsum(rng.normal(0, 0.5, days))
        df = pd.DataFrame(
            {
                "code": code,
                "date": dates.date,
                "open": close * 0.999,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 1000.0,
                "amount": close * 1000,
                "ret_1": 0.01, "ret_5": 0.02, "ret_10": 0.03, "ret_20": 0.04, "ret_60": 0.05,
                "ma_bias_5": 0.01, "ma_bias_10": 0.02, "ma_bias_20": 0.03, "ma_bias_60": 0.04,
                "ma5_gt_ma20": 1, "ma20_gt_ma60": 1,
                "vol_20": 0.02, "rsi_14": 55.0, "boll_pos": 0.5,
                "vol_ratio_5": 1.0, "amount_ratio_5": 1.0,
                "high_low_pos_20": 0.5, "range_20": 0.1,
            }
        )
        frames.append(df)
    stock = pd.concat(frames, ignore_index=True)
    bench_close = 3000 + np.arange(days) * 0.2
    bench = pd.DataFrame({"date": pd.bdate_range("2023-01-02", periods=days).date, "close": bench_close})
    return stock, bench


def test_run_walk_forward_returns_keys():
    stock, bench = _dataset()
    cfg = BacktestConfig(start="2023-06-01", end="2024-06-30", train_days=120, rebalance_days=63, top_n=2)
    results = run_walk_forward(stock, bench, cfg)
    assert set(results.keys()) == {"a", "b", "blend", "benchmark"}
    assert len(results["blend"].equity) > 0
    assert set(results["blend"].stats.keys()) >= {"total_return", "max_drawdown"}


def test_check_intraday_qualification():
    from backtest.walkforward import check_intraday_qualification

    bench_stats = {"annual_return": 0.05}
    v1_stats = {"annual_return": 0.10, "max_drawdown": -0.15}

    # Case 1: beats bench (0.12 > 0.05) and drawdown within 2pp (-0.16 vs -0.15) -> True
    v2_ok = {"annual_return": 0.12, "max_drawdown": -0.16}
    assert check_intraday_qualification(v1_stats, v2_ok, bench_stats) is True

    # Case 2: fails to beat bench (0.04 < 0.05) -> False
    v2_bad_ret = {"annual_return": 0.04, "max_drawdown": -0.14}
    assert check_intraday_qualification(v1_stats, v2_bad_ret, bench_stats) is False

    # Case 3: beats bench but drawdown worsens by > 2pp (-0.18 vs -0.15, diff = 0.03 > 0.02) -> False
    v2_bad_dd = {"annual_return": 0.15, "max_drawdown": -0.18}
    assert check_intraday_qualification(v1_stats, v2_bad_dd, bench_stats) is False

