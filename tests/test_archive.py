import pandas as pd
import pytest

from data import archive, db


def _create_synthetic_minute_bars(code: str, dates: list[str]) -> pd.DataFrame:
    rows = []
    for d in dates:
        for t in ["09:30", "09:31", "14:59", "15:00"]:
            rows.append(
                {
                    "code": code,
                    "date": d,
                    "time": t,
                    "open": 10.0,
                    "high": 10.2,
                    "low": 9.9,
                    "close": 10.1,
                    "volume": 1000,
                    "amount": 10100,
                }
            )
    return pd.DataFrame(rows)


def test_archive_when_under_limit(tmp_path):
    db_path = str(tmp_path / "market.db")
    arch_dir = str(tmp_path / "archive")
    db.init_db(db_path)

    # 3 trading dates, keep limit 5 -> should do nothing
    dates = ["2026-09-10", "2026-09-11", "2026-09-12"]
    bars = _create_synthetic_minute_bars("000001", dates)
    db.save_minute_bars(db_path, "000001", bars)

    res = archive.archive_cold_minute_bars(db_path, arch_dir, keep_trading_days=5)
    assert res["total_rows_archived"] == 0
    assert len(db.load_minute_bars(db_path, "000001")) == 12


def test_archive_and_load_seamlessly(tmp_path):
    db_path = str(tmp_path / "market.db")
    arch_dir = str(tmp_path / "archive")
    db.init_db(db_path)

    # 4 distinct dates across two months: 2026-07 (2 dates), 2026-08 (2 dates)
    # With keep_trading_days=2, the latest 2 dates (2026-08-11, 2026-08-12) stay in SQLite,
    # and the 2 older dates from 2026-07 get archived to parquet.
    dates = ["2026-07-01", "2026-07-02", "2026-08-11", "2026-08-12"]
    bars = _create_synthetic_minute_bars("000001", dates)
    db.save_minute_bars(db_path, "000001", bars)

    assert len(db.load_minute_bars(db_path, "000001")) == 16

    res = archive.archive_cold_minute_bars(db_path, arch_dir, keep_trading_days=2)
    assert res["total_rows_archived"] == 8
    assert "2026-07" in res["archived_months"]

    # Now SQLite only has the 2 hot dates (8 rows)
    hot_bars = db.load_minute_bars(db_path, "000001")
    assert len(hot_bars) == 8
    assert set(hot_bars["date"].unique()) == {"2026-08-11", "2026-08-12"}

    # Transparent load across hot + cold returns all 16 rows
    all_bars = archive.load_minute_bars_with_archive(db_path, arch_dir, "000001")
    assert len(all_bars) == 16
    assert set(all_bars["date"].unique()) == set(dates)

    # Date range filtered load
    july_bars = archive.load_minute_bars_with_archive(
        db_path, arch_dir, "000001", start_date="2026-07-01", end_date="2026-07-31"
    )
    assert len(july_bars) == 8
    assert all(d.startswith("2026-07") for d in july_bars["date"])
