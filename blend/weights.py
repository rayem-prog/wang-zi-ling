"""动态权重（见设计文档第 6 节）与合并分。"""

from __future__ import annotations

import sqlite3

import pandas as pd


def adjusted_weights(er_a: float, er_b: float, w_old: float, w_min: float = 0.2, w_max: float = 0.8) -> tuple[float, float]:
    ea = max(0.0, er_a)
    eb = max(0.0, er_b)
    wprime = 0.5 if ea + eb == 0 else ea / (ea + eb)
    wa = 0.5 * w_old + 0.5 * wprime
    wa = min(w_max, max(w_min, wa))
    return round(wa, 4), round(1 - wa, 4)


def merge_scores(df: pd.DataFrame, w_a: float) -> pd.DataFrame:
    out = df.copy()
    out["score_blend"] = w_a * out["score_a"] + (1 - w_a) * out["score_b"]
    return out


def init_weight_table(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS weight_history ("
            "ts TEXT DEFAULT (datetime('now')), w_a REAL, er_a REAL, er_b REAL)"
        )
        conn.commit()
    finally:
        conn.close()


def record_weights(db_path: str, w_a: float, er_a: float, er_b: float) -> None:
    init_weight_table(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO weight_history (w_a, er_a, er_b) VALUES (?,?,?)",
            (w_a, er_a, er_b),
        )
        conn.commit()
    finally:
        conn.close()


def load_weight_history(db_path: str) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    try:
        return pd.read_sql_query("SELECT * FROM weight_history ORDER BY ts", conn)
    finally:
        conn.close()


def current_weights(engine_settings, db_path: str) -> tuple[float, float]:
    if engine_settings.manual_override and engine_settings.manual_w_a is not None:
        wa = min(engine_settings.w_max, max(engine_settings.w_min, engine_settings.manual_w_a))
        return wa, 1 - wa
    try:
        hist = load_weight_history(db_path)
        if hist.empty:
            return engine_settings.w_a, engine_settings.w_b
        return float(hist.iloc[-1]["w_a"]), 1 - float(hist.iloc[-1]["w_a"])
    except Exception:
        return engine_settings.w_a, engine_settings.w_b
