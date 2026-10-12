"""Daemon config application, quote gating and migration in isolated directories."""
import datetime as dt
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from config.settings import Settings, save_settings, settings_version
from data import db
from scripts import intraday_daemon
from strategy.notify import CompositeNotifier, MockNotifier


@pytest.fixture(autouse=True)
def isolated_daemon(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    original_popen = intraday_daemon.subprocess.Popen
    monkeypatch.setattr(intraday_daemon.subprocess, 'Popen',
                        lambda command, *a, **k: None if command[0] == 'caffeinate' else original_popen(command, *a, **k))
    monkeypatch.setattr(intraday_daemon, 'is_trading_time', lambda *_: True)
    monkeypatch.setattr(intraday_daemon.pipeline, 'update_minute_bars', lambda *a, **k: {'failed': [], 'empty': []})
    # Freeze only daemon wall time; notifier cooldown continues using real time.
    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 9, 14, 10, 0)
            return value if tz is None else value.replace(tzinfo=tz)
    monkeypatch.setattr(intraday_daemon, 'dt', SimpleNamespace(datetime=Clock, time=dt.time))


def config(tmp_path):
    return replace(Settings(), data=replace(Settings().data, db_path=str(tmp_path / 'market.db')),
                   notify=replace(Settings().notify, backend='mock'))


def inputs(_):
    return (pd.DataFrame([{'code': '000001', 'cost': 10}]),
            pd.DataFrame([{'code': '000001', 'action': '买入', 'limit_low': 9, 'limit_high': 11}]), ['000001'])


def spot(_):
    return pd.DataFrame([{'code': '000001', 'name': 'test', 'price': 9.4, 'pct_change': -6,
                          'volume': 100, 'amount': 940, 'trade_date': '2026-09-14',
                          'quote_time': '20260914100000', 'source': 'test'}])


def run_rounds(monkeypatch, settings, changes, *, explicit=False):
    save_settings(settings)
    notifiers = []
    def build(cfg):
        value = CompositeNotifier(mode='mock', mock_notifier=MockNotifier(),
                                  p1_cooldown_minutes=cfg.notify.p1_cooldown_minutes)
        notifiers.append(value)
        return value
    monkeypatch.setattr(intraday_daemon, 'build_notifier', build)
    monkeypatch.setattr(intraday_daemon, 'load_monitor_inputs', inputs)
    monkeypatch.setattr(intraday_daemon.fetcher, 'fetch_spot_batch', spot)
    actions = iter(changes)
    def sleep(_):
        change = next(actions, None)
        if change is None:
            raise KeyboardInterrupt
        change()
    monkeypatch.setattr(intraday_daemon.time, 'sleep', sleep)
    with pytest.raises(KeyboardInterrupt):
        intraday_daemon.run_daemon(settings=settings if explicit else None, poll_interval=0)
    with sqlite3.connect(settings.data.db_path) as conn:
        heartbeats = conn.execute('SELECT status,message,config_version FROM daemon_heartbeat').fetchall()
    return notifiers, heartbeats


def test_risk_reload_applies_new_version_without_resetting_cooldown(tmp_path, monkeypatch):
    initial = config(tmp_path)
    updated = replace(initial, risk=replace(initial.risk, stop_loss=.10))
    notifiers, records = run_rounds(monkeypatch, initial, [lambda: save_settings(updated)])
    assert len(notifiers) == 1
    messages = notifiers[0].mock_notifier.sent_messages
    assert len([m for m in messages if m['level'] == 'P0']) == 1
    assert len([m for m in messages if m['level'] == 'P1']) == 1
    assert [v for state, _, v in records if state == 'running'] == [settings_version(initial), settings_version(updated)]


def test_notification_reload_rebuilds_channels(tmp_path, monkeypatch):
    initial = config(tmp_path)
    updated = replace(initial, notify=replace(initial.notify, p1_cooldown_minutes=0))
    notifiers, records = run_rounds(monkeypatch, initial, [lambda: save_settings(updated)])
    assert len(notifiers) == 2
    assert len(notifiers[1].mock_notifier.sent_messages) == 2
    assert records[-1][2] == settings_version(updated)


def test_explicit_settings_remain_fixed(tmp_path, monkeypatch):
    initial = config(tmp_path)
    updated = replace(initial, risk=replace(initial.risk, stop_loss=.10))
    notifiers, records = run_rounds(monkeypatch, initial, [lambda: save_settings(updated)], explicit=True)
    assert len([m for m in notifiers[0].mock_notifier.sent_messages if m['level'] == 'P0']) == 2
    assert {v for _, _, v in records} == {settings_version(initial)}


def test_malformed_reload_reports_error_and_keeps_last_applied_version(tmp_path, monkeypatch):
    initial = config(tmp_path)
    notifiers, records = run_rounds(monkeypatch, initial, [lambda: Path(initial.paths.user_settings_path).write_text('{broken')])
    errors = [(message, v) for state, message, v in records if state == 'error']
    assert len(errors) == 1
    assert '配置' in errors[0][0]
    assert errors[0][1] == settings_version(initial)
    assert len([m for m in notifiers[0].mock_notifier.sent_messages if m['level'] == 'P0']) == 1


def test_mixed_fresh_and_same_day_stale_quotes_only_notify_fresh(tmp_path, monkeypatch):
    settings = config(tmp_path)
    monkeypatch.setattr(intraday_daemon, 'load_monitor_inputs', lambda _: (pd.DataFrame([
        {'code': '000001', 'cost': 10}, {'code': '000002', 'cost': 10}]), pd.DataFrame(), ['000001', '000002']))
    rows = spot([])
    rows = pd.concat([rows, rows.assign(code='000002', quote_time='20260914093000')])
    monkeypatch.setattr(intraday_daemon.fetcher, 'fetch_spot_batch', lambda _: rows)
    notifier = CompositeNotifier(mode='mock', mock_notifier=MockNotifier())
    monkeypatch.setattr(intraday_daemon, 'build_notifier', lambda _: notifier)
    intraday_daemon.run_daemon(settings=settings, once=True)
    assert len(notifier.mock_notifier.sent_messages) == 1
    assert '000001' in notifier.mock_notifier.sent_messages[0]['title']
    with sqlite3.connect(settings.data.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM daemon_heartbeat WHERE status='degraded' AND message LIKE '%000002%'").fetchone()[0] == 1


def test_legacy_heartbeat_migrates_with_unknown_applied_version(tmp_path):
    path = str(tmp_path / 'legacy.db')
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE daemon_heartbeat (id INTEGER PRIMARY KEY, timestamp TEXT, status TEXT, message TEXT, pid INTEGER)')
        conn.execute("INSERT INTO daemon_heartbeat VALUES (1,'old','running','old',42)")
    db.init_db(path)
    assert db.get_latest_heartbeat(path)['config_version'] is None
    db.record_heartbeat(path, 'running', config_version='applied-version')
    assert db.get_latest_heartbeat(path)['config_version'] == 'applied-version'
