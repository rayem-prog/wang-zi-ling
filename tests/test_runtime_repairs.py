"""Regression cases for access boundaries, delivery results and async heartbeat."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
import time

from fastapi.testclient import TestClient

from api.main import app
from config.settings import Settings
from strategy import notify

MAC_SEND = notify.MacLocalNotifier.send


def test_spa_rejects_external_path(tmp_path):
    marker = tmp_path / 'private.txt'
    marker.write_text('PRIVATE_MARKER')
    response = TestClient(app).get('/' + '%2e%2e/' * 12 + str(marker).lstrip('/'))
    assert response.status_code == 404
    assert 'PRIVATE_MARKER' not in response.text


def test_spa_rejects_symlink_outside_dist(tmp_path, monkeypatch):
    from api import main
    dist = tmp_path / 'dist'; dist.mkdir()
    (dist / 'index.html').write_text('SPA')
    marker = tmp_path / 'private.txt'; marker.write_text('PRIVATE_MARKER')
    (dist / 'escape.txt').symlink_to(marker)
    monkeypatch.setattr(main, 'FRONTEND_DIST', dist)
    response = TestClient(app).get('/escape.txt')
    assert response.status_code == 404


def test_disabled_mac_channel_is_not_recreated():
    settings = replace(Settings(), notify=replace(Settings().notify, enable_mac_notify=False))
    composite = notify.build_notifier(settings)
    assert composite.local_notifier is None
    assert composite.send('title', 'body') is False
    assert composite.daily_count == 0


def test_mac_failure_does_not_report_delivery(monkeypatch):
    monkeypatch.setattr(notify.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1))
    assert MAC_SEND(notify.MacLocalNotifier(), 'title', 'body') is False


def test_test_notification_reports_disabled_as_skipped(monkeypatch):
    monkeypatch.setattr(notify.WebhookNotifier, 'send', lambda *a, **k: False)
    response = TestClient(app).post('/api/settings/notify/test', json={
        'webhook_url': 'https://example.invalid/hook', 'webhook_type': 'generic',
        'enable_mac_notify': False,
    })
    result = response.json()
    assert result['mac_ok'] is None
    assert result['success'] is False
    assert result['channels']['local'] == 'skipped'


def test_websocket_slow_status_does_not_block_loop(monkeypatch):
    from api import main
    monkeypatch.setattr(main.manager, 'active_connections', [object()])
    monkeypatch.setattr(main.status, 'get_system_status', lambda: (time.sleep(.15) or {}))
    monkeypatch.setattr(main, 'get_market_trading_status', lambda: {'is_trading': False})
    async def broadcast(payload):
        return None
    monkeypatch.setattr(main.manager, 'broadcast', broadcast)
    async def probe():
        task = asyncio.create_task(main._background_push_loop())
        start = time.perf_counter()
        await asyncio.sleep(.02)
        elapsed = time.perf_counter() - start
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert elapsed < .10
    asyncio.run(probe())


def test_settings_accept_and_report_notify_backend():
    response = TestClient(app).post('/api/settings', json={'notify_backend': 'webhook'})
    assert response.status_code == 200
    assert response.json()['settings']['notify']['backend'] == 'webhook'
    assert response.json()['config_version']
    assert TestClient(app).post('/api/settings', json={'notify_backend': 'invalid'}).status_code == 422


def test_legacy_dashboard_entry_does_not_display_ungated_csv(monkeypatch):
    from app import main as legacy
    displayed = []
    monkeypatch.setattr(legacy.st,'info',lambda message:displayed.append(message))
    monkeypatch.setattr(legacy.st,'link_button',lambda *a,**k:None)
    monkeypatch.setattr(legacy,'load_rec',lambda *a,**k: (_ for _ in ()).throw(AssertionError('ungated CSV read')))
    legacy.main()
    assert any('终端' in str(x) for x in displayed)


def test_spa_fallback_rejects_external_index_symlink(tmp_path, monkeypatch):
    from api import main
    dist = tmp_path / 'dist'; dist.mkdir()
    marker = tmp_path / 'private.txt'; marker.write_text('PRIVATE_MARKER')
    (dist / 'index.html').symlink_to(marker)
    monkeypatch.setattr(main, 'FRONTEND_DIST', dist)
    response = TestClient(app).get('/unknown-client-route')
    assert response.status_code == 404
    assert 'PRIVATE_MARKER' not in response.text


def test_concurrent_sandbox_steps_are_serialized(monkeypatch):
    from api.routers import sandbox
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    barrier = Barrier(2)
    class Session:
        cursor = 0
        def step(self, days_to_step):
            previous = self.cursor
            try:
                barrier.wait(timeout=.2)
            except Exception:
                pass
            self.cursor = previous + days_to_step
            return {'cursor': self.cursor}
    session = Session()
    monkeypatch.setattr(sandbox, 'get_or_create_sandbox', lambda:session)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _:sandbox.step_sandbox(sandbox.StepSandboxRequest(days=1)),range(2)))
    assert session.cursor == 2
    assert sorted(r['cursor'] for r in results) == [1,2]
