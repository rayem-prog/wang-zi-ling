"""跑一次 walk-forward 并存 artifacts。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import json

from .walkforward import BacktestConfig, run_walk_forward


def run_and_save(features_df: pd.DataFrame, bench_df: pd.DataFrame, out_dir: str = "artifacts", cfg: BacktestConfig | None = None, settings=None) -> dict:
    cfg = cfg or BacktestConfig()
    results = run_walk_forward(features_df, bench_df, cfg, settings=settings)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    for key, res in results.items():
        p = out / f"equity_{key}.csv"
        res.equity.rename("equity").to_csv(p)
        paths[key] = str(p)
        if not res.valuations.empty:
            res.valuations.to_csv(out / f"valuations_{key}.csv")
        if res.decisions:
            (out / f"decisions_{key}.json").write_text(json.dumps(res.decisions, ensure_ascii=False, indent=2), encoding="utf-8")
        res.trades.to_csv(out / f"orders_audit_{key}.csv", index=False)
    stats = {key: res.stats for key, res in results.items()}
    pd.DataFrame(stats).to_json(out / "backtest_stats.json", orient="index")
    return paths
