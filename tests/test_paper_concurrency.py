"""Real SQLite ledger races, without a read barrier inside a write lock."""

from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading

import pytest

from strategy import paper


def _order(path, code, side, price=8.0):
    try:
        return paper.execute_paper_order(path, code, code, side, 100, price)
    except ValueError as exc:
        return exc


def _race_before_account_write(monkeypatch):
    """Old code reads twice; locked code lets the first writer time out alone.

    Trace callbacks run before SQLite steps the UPDATE. With BEGIN IMMEDIATE,
    the second operation cannot reach this point until the first commits.
    The bounded barrier thus reproduces stale reads without a lock deadlock.
    """
    original_conn = paper._conn
    barrier = threading.Barrier(2, timeout=1)

    def traced_conn(path):
        conn = original_conn(path)
        synchronized = False

        def trace(sql):
            nonlocal synchronized
            if not synchronized and sql.startswith("UPDATE paper_account SET cash"):
                synchronized = True
                try:
                    barrier.wait()
                except threading.BrokenBarrierError:
                    pass

        conn.set_trace_callback(trace)
        return conn

    monkeypatch.setattr(paper, "_conn", traced_conn)


def _run_pair(first, second):
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(first), workers.submit(second)]
        return [future.result(timeout=5) for future in futures]


def test_concurrent_buys_cannot_spend_the_same_cash(tmp_path, monkeypatch):
    path = str(tmp_path / "paper.db")
    paper.reset_paper_account(path, 1000)
    _race_before_account_write(monkeypatch)

    results = _run_pair(lambda: _order(path, "000001", "BUY"),
                        lambda: _order(path, "000002", "BUY"))

    assert sum(isinstance(result, dict) for result in results) == 1
    failures = [result for result in results if isinstance(result, ValueError)]
    assert len(failures) == 1
    assert "可用资金不足" in str(failures[0])
    account = paper.get_paper_account(path)
    assert account["cash"] == 200
    assert account["market_value"] == 800
    assert account["total_equity"] == 1000
    assert account["total_pnl"] == 0
    assert len(account["orders"]) == 1


def test_concurrent_buys_of_same_code_preserve_cash_and_shares(tmp_path, monkeypatch):
    path = str(tmp_path / "paper.db")
    paper.reset_paper_account(path, 2000)
    _race_before_account_write(monkeypatch)

    results = _run_pair(lambda: _order(path, "000001", "BUY"),
                        lambda: _order(path, "000001", "BUY"))

    assert all(isinstance(result, dict) for result in results)
    account = paper.get_paper_account(path)
    assert account["cash"] == 400
    assert account["market_value"] == 1600
    assert account["total_equity"] == 2000
    assert account["positions"][0]["shares"] == 200
    assert account["positions"][0]["avg_cost"] == 8
    assert len(account["orders"]) == 2


def test_concurrent_sells_cannot_sell_the_same_shares_twice(tmp_path, monkeypatch):
    path = str(tmp_path / "paper.db")
    paper.reset_paper_account(path, 1000)
    _order(path, "000001", "BUY")
    _race_before_account_write(monkeypatch)

    results = _run_pair(lambda: _order(path, "000001", "SELL", 10),
                        lambda: _order(path, "000001", "SELL", 10))

    assert sum(isinstance(result, dict) for result in results) == 1
    failures = [result for result in results if isinstance(result, ValueError)]
    assert len(failures) == 1
    assert "持仓不足" in str(failures[0])
    account = paper.get_paper_account(path)
    assert account["cash"] == 1200
    assert account["positions"] == []
    assert account["realized_pnl"] == 200
    assert account["total_pnl"] == 200
    assert len(account["orders"]) == 2


def test_concurrent_buy_and_sell_preserve_cash_and_profit(tmp_path, monkeypatch):
    path = str(tmp_path / "paper.db")
    paper.reset_paper_account(path, 2000)
    _order(path, "000001", "BUY", 10)
    _race_before_account_write(monkeypatch)

    results = _run_pair(lambda: _order(path, "000001", "BUY", 8),
                        lambda: _order(path, "000001", "SELL", 10))

    assert all(isinstance(result, dict) for result in results)
    account = paper.get_paper_account(path, {"000001": 10})
    assert account["cash"] == 1200
    assert account["positions"][0]["shares"] == 100
    assert account["positions"][0]["avg_cost"] in (8, 9)
    assert account["total_equity"] == 2200
    assert account["total_pnl"] == 200
    assert account["realized_pnl"] + account["unrealized_pnl"] == 200
    assert len(account["orders"]) == 3


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_reset_cannot_leave_an_order_based_on_pre_reset_cash(tmp_path, monkeypatch, side):
    path = str(tmp_path / "paper.db")
    paper.reset_paper_account(path, 1000)
    if side == "SELL":
        _order(path, "000001", "BUY")
    original_conn = paper._conn
    order_at_write = threading.Event()
    reset_done = threading.Event()

    def traced_conn(path):
        conn = original_conn(path)

        def trace(sql):
            if sql.startswith("UPDATE paper_account SET cash"):
                order_at_write.set()
                # Old code allows reset to finish here. Fixed code holds the
                # write lock, so its order commits before reset can complete.
                reset_done.wait(timeout=1)

        conn.set_trace_callback(trace)
        return conn

    monkeypatch.setattr(paper, "_conn", traced_conn)

    def reset():
        try:
            return paper.reset_paper_account(path, 500)
        finally:
            reset_done.set()

    with ThreadPoolExecutor(max_workers=2) as workers:
        order_future = workers.submit(_order, path, "000001", side, 8 if side == "BUY" else 10)
        assert order_at_write.wait(timeout=3)
        reset_future = workers.submit(reset)
        assert order_future.result(timeout=5)["status"] == "success"
        assert reset_future.result(timeout=5)["status"] == "reset_success"

    account = paper.get_paper_account(path)
    assert account["initial_cash"] == 500
    assert account["cash"] == 500
    assert account["total_equity"] == 500
    assert account["positions"] == []
    assert account["orders"] == []
    assert account["realized_pnl"] == 0


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_failed_order_rolls_back_every_ledger_change(tmp_path, side):
    path = str(tmp_path / "paper.db")
    paper.reset_paper_account(path, 2000)
    _order(path, "000001", "BUY")
    before = paper.get_paper_account(path)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TRIGGER reject_order BEFORE INSERT ON paper_orders "
                     "BEGIN SELECT RAISE(ABORT, 'controlled ledger failure'); END")

    with pytest.raises(sqlite3.IntegrityError, match="controlled ledger failure"):
        paper.execute_paper_order(path, "000001", "test", side, 100, 10)

    assert paper.get_paper_account(path) == before


def test_failed_reset_rolls_back_all_deleted_ledger_rows(tmp_path):
    path = str(tmp_path / "paper.db")
    paper.reset_paper_account(path, 2000)
    paper.execute_paper_order(path, "000001", "test", "BUY", 200, 8)
    paper.execute_paper_order(path, "000001", "test", "SELL", 100, 10)
    before = paper.get_paper_account(path)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TRIGGER reject_reset BEFORE DELETE ON paper_realized "
                     "BEGIN SELECT RAISE(ABORT, 'controlled reset failure'); END")

    with pytest.raises(sqlite3.IntegrityError, match="controlled reset failure"):
        paper.reset_paper_account(path, 500)

    assert paper.get_paper_account(path) == before


def test_concurrent_first_account_initialization(tmp_path, monkeypatch):
    path = str(tmp_path / 'fresh.db')
    # Build the schema, then remove only the account to model first access.
    with paper._conn(path) as conn:
        conn.execute('DELETE FROM paper_account')
    original_connect = sqlite3.connect
    barrier = threading.Barrier(2, timeout=1)
    def connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        def trace(sql):
            if sql.startswith('INSERT INTO paper_account'):
                try:
                    barrier.wait()
                except threading.BrokenBarrierError:
                    pass
        conn.set_trace_callback(trace)
        return conn
    monkeypatch.setattr(paper.sqlite3, 'connect', connect)
    results = _run_pair(lambda:paper.get_paper_account(path), lambda:paper.get_paper_account(path))
    assert all(r['cash'] == paper.DEFAULT_INITIAL_CASH for r in results)
    with original_connect(path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM paper_account').fetchone()[0] == 1
