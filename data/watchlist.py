"""Personal watchlist storage; SQL and connection ownership stay here."""
from __future__ import annotations
from contextlib import contextmanager
import datetime as dt
import sqlite3


class WatchlistRepository:
    def __init__(self, db_path: str):
        self.db_path = db_path

    @contextmanager
    def _connection(self):
        # sqlite3's context manager commits/rolls back but does not close.
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                conn.execute("""CREATE TABLE IF NOT EXISTS user_watchlist (
                    code TEXT PRIMARY KEY, name TEXT, added_at TEXT
                )""")
                yield conn
        finally:
            conn.close()

    def list(self) -> list[dict]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT code, name, added_at FROM user_watchlist ORDER BY added_at DESC"
            ).fetchall()
            return [dict(row) for row in rows]

    def add(self, code: str, name: str) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO user_watchlist (code, name, added_at) VALUES (?, ?, ?)",
                (code, name, dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )

    def remove(self, code: str) -> None:
        with self._connection() as conn:
            conn.execute("DELETE FROM user_watchlist WHERE code = ?", (code,))
