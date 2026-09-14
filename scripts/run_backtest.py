"""跑完整 walk-forward 回测：先补数据，再算特征，最后输出 artifacts。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from backtest.runner import run_and_save
from backtest.walkforward import BacktestConfig
from config.settings import load_settings
from data import db, pipeline
from features.alpha import compute_features


def main() -> None:
    settings = load_settings()
    pipeline.update_all(settings)
    codes = db.list_codes(settings.data.db_path)
    frames = []
    for code in codes:
        if code == settings.data.index_symbol:
            continue
        bars = db.load_bars(settings.data.db_path, code)
        if len(bars) < 250:
            continue
        frames.append(compute_features(bars))
    if not frames:
        raise SystemExit("历史数据不足：请确认 update_data 成功且数据超过 250 个交易日")
    features = pd.concat(frames, ignore_index=True)
    bench = db.load_bars(settings.data.db_path, settings.data.index_symbol)
    if bench.empty:
        raise SystemExit("缺少基准指数数据，请检查 update_all 中的指数拉取")
    cfg = BacktestConfig(start="2021-01-01", end="2024-12-31")
    print(run_and_save(features, bench, out_dir=settings.paths.artifact_dir, cfg=cfg))


if __name__ == "__main__":
    main()
