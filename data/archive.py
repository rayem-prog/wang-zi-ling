"""冷热分层与分钟线数据归档：保留最近 N 交易日热数据于 SQLite，更早历史按月压缩归档至 Parquet (zstd)。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from data import db
from data.db import MINUTE_BAR_COLUMNS, _connect


def archive_cold_minute_bars(
    db_path: str,
    archive_dir: str = "data/archive",
    keep_trading_days: int = 60,
) -> dict:
    conn = _connect(db_path)
    archived_months = []
    total_rows_archived = 0

    try:
        rows = conn.execute(
            "SELECT DISTINCT date FROM minute_bars ORDER BY date DESC"
        ).fetchall()
        dates = [r["date"] for r in rows]

        if len(dates) <= keep_trading_days:
            return {"archived_months": [], "total_rows_archived": 0}

        cutoff_date = dates[keep_trading_days - 1]
        cold_dates = [d for d in dates if d < cutoff_date]
        if not cold_dates:
            return {"archived_months": [], "total_rows_archived": 0}

        # Group by YYYY-MM
        months = sorted(set(d[:7] for d in cold_dates))
        arch_dir = Path(archive_dir)
        arch_dir.mkdir(parents=True, exist_ok=True)

        for m in months:
            # Query cold data for month m
            sql = "SELECT * FROM minute_bars WHERE date LIKE ? AND date < ?"
            month_pattern = f"{m}%"
            df = pd.read_sql_query(sql, conn, params=[month_pattern, cutoff_date])
            if df.empty:
                continue

            target_file = arch_dir / f"minute_{m}.parquet.zst"

            # If existing file exists, merge
            if target_file.exists():
                old_df = pd.read_parquet(target_file)
                combined = pd.concat([old_df, df], ignore_index=True)
                combined = combined.drop_duplicates(subset=["code", "date", "time"], keep="last").reset_index(drop=True)
            else:
                combined = df

            combined.to_parquet(target_file, engine="pyarrow", compression="zstd")

            # Verification: Read back and check
            read_back = pd.read_parquet(target_file)
            check = read_back.set_index(["code", "date", "time"]).loc[df.set_index(["code", "date", "time"]).index]
            pd.testing.assert_frame_equal(check[df.columns.drop(["code", "date", "time"])].reset_index(drop=True),
                                          df.drop(columns=["code", "date", "time"]).reset_index(drop=True), check_dtype=False)

            # Verified: safely remove from SQLite
            conn.execute(
                "DELETE FROM minute_bars WHERE date LIKE ? AND date < ?",
                (month_pattern, cutoff_date),
            )
            conn.commit()

            archived_months.append(m)
            total_rows_archived += len(df)

        if total_rows_archived > 0:
            try:
                conn.execute("VACUUM")
            except Exception:
                pass

    finally:
        conn.close()

    return {
        "archived_months": archived_months,
        "total_rows_archived": total_rows_archived,
        "cutoff_date": cutoff_date if "cutoff_date" in locals() else None,
    }


def load_minute_bars_with_archive(
    db_path: str,
    archive_dir: str,
    code: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    hot_df = db.load_minute_bars(db_path, code, start_date=start_date, end_date=end_date, include_metadata=True)

    arch_dir = Path(archive_dir)
    cold_frames = []

    if arch_dir.exists():
        for p in sorted(arch_dir.glob("minute_*.parquet*")):
            try:
                cdf = pd.read_parquet(p)
                cdf = cdf[cdf["code"] == code]
                if start_date:
                    cdf = cdf[cdf["date"] >= str(start_date)]
                if end_date:
                    cdf = cdf[cdf["date"] <= str(end_date)]
                if not cdf.empty:
                    cold_frames.append(cdf)
            except Exception:
                continue

    if not cold_frames:
        out = hot_df[MINUTE_BAR_COLUMNS]
        out.attrs.update(hot_df.attrs)
        return out

    all_frames = cold_frames + ([hot_df] if not hot_df.empty else [])
    merged = pd.concat(all_frames, ignore_index=True)
    merged = merged.drop_duplicates(subset=["code", "date", "time"], keep="last")
    out = merged[MINUTE_BAR_COLUMNS].sort_values(["date", "time"]).reset_index(drop=True)
    out.attrs["verification_status"] = "verified" if "verification_status" in merged and merged["verification_status"].eq("verified").all() else "quarantined"
    out.attrs["quarantined_rows"] = int(merged["verification_status"].fillna("quarantined").ne("verified").sum()) if "verification_status" in merged else len(merged)
    return out
