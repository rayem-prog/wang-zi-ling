"""SQLite 本地存储：日线 bar、盘中快照、数据新鲜度。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pandas as pd

BAR_COLUMNS = ["code", "date", "open", "high", "low", "close", "volume", "amount"]
MINUTE_BAR_COLUMNS = ["code", "date", "time", "open", "high", "low", "close", "volume", "amount"]


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str) -> None:
    conn = _connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS bars (
                code TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL,
                volume REAL, amount REAL,
                PRIMARY KEY (code, date)
            );
            CREATE TABLE IF NOT EXISTS spot (
                code TEXT PRIMARY KEY,
                name TEXT, price REAL, pct_change REAL,
                volume REAL, amount REAL, ts TEXT
            );
            CREATE TABLE IF NOT EXISTS freshness (
                code TEXT PRIMARY KEY,
                last_updated TEXT
            );
            CREATE TABLE IF NOT EXISTS minute_bars (
                code TEXT NOT NULL,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL,
                volume REAL, amount REAL,
                PRIMARY KEY (code, date, time)
            );
            CREATE INDEX IF NOT EXISTS idx_minute_bars_code_date ON minute_bars(code, date);
            CREATE TABLE IF NOT EXISTS daemon_heartbeat (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT,
                pid INTEGER
            );
            CREATE TABLE IF NOT EXISTS macro_history (
                date TEXT PRIMARY KEY,
                score REAL NOT NULL,
                stance TEXT NOT NULL,
                trend REAL,
                breadth REAL,
                vol REAL,
                details TEXT
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def save_bars(db_path: str, code: str, bars: pd.DataFrame) -> int:
    conn = _connect(db_path)
    rows = []
    for _, r in bars.iterrows():
        rows.append(
            (
                code,
                str(r["date"]),
                float(r["open"]), float(r["high"]), float(r["low"]),
                float(r["close"]), float(r["volume"]), float(r["amount"]),
            )
        )
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO bars (code,date,open,high,low,close,volume,amount) "
            "VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def load_bars(db_path: str, code: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    conn = _connect(db_path)
    sql = "SELECT code,date,open,high,low,close,volume,amount FROM bars WHERE code=?"
    params: list = [code]
    if start:
        sql += " AND date>=?"
        params.append(start)
    if end:
        sql += " AND date<=?"
        params.append(end)
    sql += " ORDER BY date ASC"
    try:
        df = pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df[BAR_COLUMNS]


def mark_fresh(db_path: str, code: str) -> None:
    conn = _connect(db_path)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        conn.execute(
            "INSERT OR REPLACE INTO freshness (code,last_updated) VALUES (?,?)",
            (code, ts),
        )
        conn.commit()
    finally:
        conn.close()


def get_freshness(db_path: str, code: str) -> str | None:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT last_updated FROM freshness WHERE code=?", (code,)).fetchone()
        return row["last_updated"] if row else None
    finally:
        conn.close()


def save_spot(db_path: str, quotes: pd.DataFrame) -> None:
    conn = _connect(db_path)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO spot (code,name,price,pct_change,volume,amount,ts) "
            "VALUES (?,?,?,?,?,?,?)",
            [
                (r["code"], r["name"], r["price"], r["pct_change"], r["volume"], r["amount"], ts)
                for _, r in quotes.iterrows()
            ],
        )
        conn.commit()
    finally:
        conn.close()


def load_spot(db_path: str) -> pd.DataFrame:
    conn = _connect(db_path)
    try:
        return pd.read_sql_query("SELECT * FROM spot ORDER BY code", conn)
    finally:
        conn.close()


def list_codes(db_path: str) -> list[str]:
    conn = _connect(db_path)
    try:
        rows = conn.execute("SELECT code FROM bars UNION SELECT code FROM spot ORDER BY code").fetchall()
        return [r["code"] for r in rows]
    finally:
        conn.close()


def get_latest_bar_date(db_path: str, code: str) -> str | None:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT MAX(date) AS max_date FROM bars WHERE code=?", (code,)).fetchone()
        return row["max_date"] if row and row["max_date"] else None
    finally:
        conn.close()


def save_minute_bars(db_path: str, code: str, bars: pd.DataFrame) -> int:
    if bars.empty:
        return 0
    conn = _connect(db_path)
    rows = []
    for _, r in bars.iterrows():
        rows.append(
            (
                code,
                str(r["date"]),
                str(r["time"]),
                float(r["open"]),
                float(r["high"]),
                float(r["low"]),
                float(r["close"]),
                float(r["volume"]),
                float(r["amount"]),
            )
        )
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO minute_bars (code,date,time,open,high,low,close,volume,amount) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def load_minute_bars(
    db_path: str,
    code: str,
    date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    conn = _connect(db_path)
    sql = "SELECT code,date,time,open,high,low,close,volume,amount FROM minute_bars WHERE code=?"
    params: list = [code]
    if date:
        sql += " AND date=?"
        params.append(str(date))
    if start_date:
        sql += " AND date>=?"
        params.append(str(start_date))
    if end_date:
        sql += " AND date<=?"
        params.append(str(end_date))
    sql += " ORDER BY date ASC, time ASC"
    try:
        df = pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()
    if df.empty:
        return pd.DataFrame(columns=MINUTE_BAR_COLUMNS)
    return df[MINUTE_BAR_COLUMNS]


def get_latest_minute_date(db_path: str, code: str) -> str | None:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT MAX(date) AS max_date FROM minute_bars WHERE code=?", (code,)).fetchone()
        return row["max_date"] if row and row["max_date"] else None
    finally:
        conn.close()


def record_heartbeat(db_path: str, status: str, message: str = "", pid: int = 0) -> None:
    conn = _connect(db_path)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        conn.execute(
            "INSERT INTO daemon_heartbeat (timestamp, status, message, pid) VALUES (?,?,?,?)",
            (ts, status, message, pid),
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_heartbeat(db_path: str) -> dict | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, timestamp, status, message, pid FROM daemon_heartbeat ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return dict(row)
    finally:
        conn.close()


def save_macro_score(
    db_path: str,
    date: str,
    score: float,
    stance: str,
    trend: float,
    breadth: float,
    vol: float,
    details: str = "",
) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO macro_history (date, score, stance, trend, breadth, vol, details) "
            "VALUES (?,?,?,?,?,?,?)",
            (str(date), float(score), str(stance), float(trend), float(breadth), float(vol), str(details)),
        )
        conn.commit()
    finally:
        conn.close()


def load_macro_history(db_path: str, limit: int = 30) -> pd.DataFrame:
    conn = _connect(db_path)
    try:
        return pd.read_sql_query(
            "SELECT date, score, stance, trend, breadth, vol, details FROM macro_history "
            "ORDER BY date DESC LIMIT ?",
            conn,
            params=[limit],
        )
    finally:
        conn.close()

