import datetime as dt
from pathlib import Path
import pandas as pd

from config.settings import Settings
from data import db
from features.alpha import compute_features
from scripts import daily_update


def test_module_importable():
    assert callable(getattr(daily_update, "run_daily", None))


def test_run_daily_flow(monkeypatch, tmp_path):
    db_path = str(tmp_path / "market.db")
    art_dir = str(tmp_path / "artifacts")
    s = Settings(
        data=Settings().data.__class__(db_path=db_path),
        paths=Settings().paths.__class__(artifact_dir=art_dir),
        notify=Settings().notify.__class__(backend="mock"),
    )
    db.init_db(db_path)

    # Populate 70 days of bars for 000001 and sh000300
    dates = pd.bdate_range("2026-01-01", periods=70)
    stock_bars = pd.DataFrame(
        {
            "code": "000001",
            "date": dates.date,
            "open": [10.0 + i * 0.1 for i in range(70)],
            "high": [10.2 + i * 0.1 for i in range(70)],
            "low": [9.9 + i * 0.1 for i in range(70)],
            "close": [10.1 + i * 0.1 for i in range(70)],
            "volume": [1000.0] * 70,
            "amount": [10100.0] * 70,
        }
    )
    bench_bars = pd.DataFrame(
        {
            "code": "sh000300",
            "date": dates.date,
            "open": [3000.0 + i * 5 for i in range(70)],
            "high": [3010.0 + i * 5 for i in range(70)],
            "low": [2990.0 + i * 5 for i in range(70)],
            "close": [3005.0 + i * 5 for i in range(70)],
            "volume": [10000.0] * 70,
            "amount": [30000000.0] * 70,
        }
    )
    db.save_bars(db_path, "000001", stock_bars)
    db.save_bars(db_path, "sh000300", bench_bars)

    # Monkeypatch pipeline
    monkeypatch.setattr(
        daily_update.pipeline,
        "update_all",
        lambda settings: {"updated": ["000001"], "failed": [], "bars_added": 0, "fresh_codes": ["000001"]},
    )
    monkeypatch.setattr(
        daily_update.pipeline,
        "spot_snapshot",
        lambda settings, codes: pd.DataFrame(
            [{"code": "000001", "name": "平安银行", "price": 17.1, "pct_change": 1.0, "volume": 1000, "amount": 17100}]
        ),
    )
    # Redirect paths
    monkeypatch.setattr(daily_update, "SCORES_PATH", str(tmp_path / "artifacts" / "latest_scores.csv"))
    monkeypatch.setattr(daily_update, "REC_PATH", str(tmp_path / "artifacts" / "recommendations.csv"))
    monkeypatch.setattr(daily_update, "ORDERS_PATH", str(tmp_path / "artifacts" / "latest_orders.csv"))

    res = daily_update.run_daily(s)
    assert res["updated"] == 1
    assert "macro_stance" in res
    assert "macro_score" in res
    assert Path(tmp_path / "artifacts" / "latest_orders.csv").exists()

    last_d = res["last_date"]
    assert Path(tmp_path / "artifacts" / "reports" / f"{last_d}.md").exists()

