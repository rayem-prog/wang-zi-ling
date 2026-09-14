"""跑一次 walk-forward 并存 artifacts。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .walkforward import BacktestConfig, run_walk_forward


def run_and_save(features_df: pd.DataFrame, bench_df: pd.DataFrame, out_dir: str = "artifacts", cfg: BacktestConfig | None = None) -> dict:
    cfg = cfg or BacktestConfig()
    results = run_walk_forward(features_df, bench_df, cfg)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    for key, res in results.items():
        p = out / f"equity_{key}.csv"
        res.equity.rename("equity").to_csv(p)
        paths[key] = str(p)
    stats = {key: res.stats for key, res in results.items()}
    pd.DataFrame(stats).to_json(out / "backtest_stats.json", orient="index")
    return paths
