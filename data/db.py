"""SQLite 本地存储：日线 bar、盘中快照、数据新鲜度。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

BAR_COLUMNS = ["code", "date", "open", "high", "low", "close", "volume", "amount"]
MINUTE_BAR_COLUMNS = ["code", "date", "time", "open", "high", "low", "close", "volume", "amount"]


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(db_path)
    try:
        # Historical audits must not block a snapshot commit with a shared read lock.
        conn.execute('PRAGMA journal_mode=WAL')
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
            CREATE TABLE IF NOT EXISTS collection_universe (
                config TEXT PRIMARY KEY,
                codes TEXT NOT NULL
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
                pid INTEGER,
                config_version TEXT
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
            CREATE TABLE IF NOT EXISTS data_fetch_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
                source TEXT, status TEXT NOT NULL, rows_added INTEGER NOT NULL,
                attempted_at TEXT NOT NULL, error TEXT
            );
            """
        )
        for table in ("bars", "minute_bars"):
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if "source" not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN source TEXT DEFAULT 'legacy_unknown'")
            if "fetched_at" not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN fetched_at TEXT")
        heartbeat_columns = {row[1] for row in conn.execute("PRAGMA table_info(daemon_heartbeat)")}
        if "config_version" not in heartbeat_columns:
            conn.execute("ALTER TABLE daemon_heartbeat ADD COLUMN config_version TEXT")
        log_columns = {row[1] for row in conn.execute("PRAGMA table_info(data_fetch_log)")}
        if "kind" not in log_columns:
            conn.execute("ALTER TABLE data_fetch_log ADD COLUMN kind TEXT DEFAULT 'daily'")
        spot_columns = {row[1] for row in conn.execute("PRAGMA table_info(spot)")}
        for column in ("source", "trade_date", "quote_time"):
            if column not in spot_columns:
                conn.execute(f"ALTER TABLE spot ADD COLUMN {column} TEXT")
        from data.baseline import migrate
        migrate(conn)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS market_input_version (
                id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL,
                daily_version INTEGER NOT NULL DEFAULT 0, minute_version INTEGER NOT NULL DEFAULT 0);
            INSERT OR IGNORE INTO market_input_version(id,version) VALUES(1,0);
        """)
        conn.execute('BEGIN IMMEDIATE')
        version_columns={row[1] for row in conn.execute('PRAGMA table_info(market_input_version)')}
        for column in ('daily_version','minute_version'):
            if column not in version_columns:
                conn.execute(f'ALTER TABLE market_input_version ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0')
                conn.execute(f'UPDATE market_input_version SET {column}=version WHERE id=1')
        for table in ("bars", "minute_bars"):
            component='daily_version' if table=='bars' else 'minute_version'
            for operation in ("INSERT", "UPDATE", "DELETE"):
                name=f'market_version_{table}_{operation.lower()}'
                old=conn.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",(name,)).fetchone()
                if old and component not in old[0]:
                    conn.execute(f'DROP TRIGGER {name}')
                conn.execute(f"""CREATE TRIGGER IF NOT EXISTS market_version_{table}_{operation.lower()}
                    AFTER {operation} ON {table} BEGIN
                        UPDATE market_input_version SET version=version+1,{component}={component}+1 WHERE id=1;
                    END""")
        conn.commit()
    finally:
        conn.close()


def save_bars(db_path: str, code: str, bars: pd.DataFrame, source: str = "manual") -> int:
    if source.lower() in {"simulation", "demo", "synthetic"}:
        raise ValueError("模拟数据不能写入真实行情表")
    conn = _connect(db_path)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for _, r in bars.iterrows():
        rows.append(
            (
                code,
                str(r["date"]),
                float(r["open"]), float(r["high"]), float(r["low"]),
                float(r["close"]), float(r["volume"]), float(r["amount"]),
                source, fetched_at,
            )
        )
    try:
        from data.baseline import save_rows, capture_batch
        names = BAR_COLUMNS + ["source", "fetched_at"]
        with conn:
            capture_batch(conn, bars)
            added = save_rows(conn, "bars", [dict(zip(names, row)) for row in rows],
                              adjustment=bars.attrs.get("adjustment", "unknown"),
                              adjustment_version=bars.attrs.get("adjustment_version", "unknown"),
                              evidence=bars.attrs.get("evidence", ""))
        return added
    finally:
        conn.close()


def load_bars(db_path: str, code: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    conn = _connect(db_path)
    sql = ("SELECT * FROM bars WHERE code=? "
           "AND coalesce(source, '') NOT IN ('simulation','demo','synthetic')")
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
    out = df[BAR_COLUMNS]
    out.attrs["verification_status"] = "verified" if "verification_status" in df and df["verification_status"].eq("verified").all() else "quarantined"
    out.attrs["quarantined_rows"] = int(df["verification_status"].ne("verified").sum()) if "verification_status" in df else len(df)
    return out


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


def universe_key(universe) -> str:
    from dataclasses import asdict
    import json
    return json.dumps(asdict(universe), sort_keys=True)


def save_collection_universe(db_path: str, universe, codes: list[str]) -> None:
    import json
    if not codes:
        raise ValueError('证券池为空，保留上次采集范围')
    init_db(db_path)
    conn = _connect(db_path)
    try:
        conn.execute('INSERT OR REPLACE INTO collection_universe(config,codes) VALUES (?,?)',
                     (universe_key(universe), json.dumps(sorted(set(codes)))))
        conn.commit()
    finally:
        conn.close()


def save_spot(db_path: str, quotes: pd.DataFrame) -> None:
    conn = _connect(db_path)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO spot (code,name,price,pct_change,volume,amount,ts,source,trade_date,quote_time) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (r["code"], r["name"], r["price"], r["pct_change"], r["volume"], r["amount"], ts,
                 quotes.attrs.get("source", "unknown"), r.get("trade_date"), r.get("quote_time"))
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
        row = conn.execute("SELECT MAX(date) AS max_date FROM bars WHERE code=? "
                           "AND coalesce(source, '') NOT IN ('simulation','demo','synthetic')", (code,)).fetchone()
        return row["max_date"] if row and row["max_date"] else None
    finally:
        conn.close()


def get_first_bar_date(db_path: str, code: str) -> str | None:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT MIN(date) FROM bars WHERE code=? "
                           "AND coalesce(source, '') NOT IN ('simulation','demo','synthetic')", (code,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def get_latest_bar_metadata(db_path: str, code: str) -> dict | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT date,source,fetched_at,verification_status,quarantine_reason,adjustment,adjustment_version,evidence FROM bars WHERE code=? "
            "AND coalesce(source, '') NOT IN ('simulation','demo','synthetic') ORDER BY date DESC LIMIT 1", (code,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def record_fetch(db_path: str, code: str, start: str, end: str, source: str,
                 status: str, rows_added: int, error: str = "", kind: str = "daily") -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO data_fetch_log (code,start_date,end_date,source,status,rows_added,attempted_at,error,kind) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (code, start, end, source, status, rows_added,
             datetime.now(timezone.utc).isoformat(timespec="seconds"), error[:500], kind),
        )
        conn.commit()
    finally:
        conn.close()


def save_minute_bars(db_path: str, code: str, bars: pd.DataFrame, source: str = "manual") -> int:
    if source.lower() in {"simulation", "demo", "synthetic", "tencent_price_line"}:
        raise ValueError("模拟数据或非 OHLC 价格点不能写入真实分钟 K 线表")
    if bars.empty:
        return 0
    conn = _connect(db_path)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
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
                None if pd.isna(r["amount"]) else float(r["amount"]),
                source,
                fetched_at,
            )
        )
    try:
        from data.baseline import save_rows, capture_batch
        names = MINUTE_BAR_COLUMNS + ["source", "fetched_at"]
        with conn:
            capture_batch(conn, bars)
            added = save_rows(conn, "minute_bars", [dict(zip(names, row)) for row in rows],
                              adjustment=bars.attrs.get("adjustment", "unknown"),
                              adjustment_version=bars.attrs.get("adjustment_version", "unknown"),
                              evidence=bars.attrs.get("evidence", ""))
        return added
    finally:
        conn.close()


def load_minute_bars(
    db_path: str,
    code: str,
    date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    include_metadata: bool = False,
) -> pd.DataFrame:
    conn = _connect(db_path)
    sql = ("SELECT * FROM minute_bars WHERE code=? "
           "AND coalesce(source, '') NOT IN ('simulation','demo','synthetic','tencent_price_line')")
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
    out = df if include_metadata else df[MINUTE_BAR_COLUMNS]
    out.attrs["verification_status"] = "verified" if "verification_status" in df and df["verification_status"].eq("verified").all() else "quarantined"
    out.attrs["quarantined_rows"] = int(df["verification_status"].ne("verified").sum()) if "verification_status" in df else len(df)
    return out


def get_latest_minute_date(db_path: str, code: str) -> str | None:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT MAX(date) AS max_date FROM minute_bars WHERE code=? "
                           "AND coalesce(source, '') NOT IN ('simulation','demo','synthetic','tencent_price_line')", (code,)).fetchone()
        return row["max_date"] if row and row["max_date"] else None
    finally:
        conn.close()


def get_latest_minute_metadata(db_path: str, code: str) -> dict | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT date,time,source,fetched_at,verification_status,quarantine_reason,adjustment,adjustment_version,evidence FROM minute_bars WHERE code=? "
            "AND coalesce(source, '') NOT IN ('simulation','demo','synthetic','tencent_price_line') "
            "ORDER BY date DESC,time DESC LIMIT 1", (code,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def record_heartbeat(db_path: str, status: str, message: str = "", pid: int = 0,
                     config_version: str | None = None) -> None:
    conn = _connect(db_path)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        conn.execute(
            "INSERT INTO daemon_heartbeat (timestamp, status, message, pid, config_version) VALUES (?,?,?,?,?)",
            (ts, status, message, pid, config_version),
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_heartbeat(db_path: str) -> dict | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, timestamp, status, message, pid, config_version FROM daemon_heartbeat ORDER BY id DESC LIMIT 1"
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


# Explicit trusted entrypoints; raw load_bars remains available for quarantined chart inspection.
from data.baseline import audit_baseline, load_training_bars, publish_bar_snapshot, correct_bar_record, load_training_minute_bars
