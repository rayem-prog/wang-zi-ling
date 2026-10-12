"""Daily generation must finish once even when a browser retries."""
import threading
import time
from fastapi.testclient import TestClient
from api.main import app


def test_daily_request_returns_before_work_finishes_and_deduplicates(monkeypatch):
    from api.routers import recap
    entered = threading.Event(); release = threading.Event(); calls = []
    def work(**kwargs):
        calls.append(kwargs); entered.set(); release.wait(3)
        return {'last_date': '2026-09-14'}
    monkeypatch.setattr(recap.daily_update, 'run_daily', work)
    client = TestClient(app)
    try:
        start = time.perf_counter()
        first = client.post('/api/recap/run?fast_mode=true').json()
        assert time.perf_counter() - start < .5
        assert entered.wait(1)
        second = client.post('/api/recap/run?fast_mode=false').json()
        assert second['job_id'] == first['job_id']
        assert len(calls) == 1
        release.set()
        for _ in range(100):
            state = client.get('/api/recap/jobs/' + first['job_id']).json()
            if state['status'] in ('succeeded', 'failed'): break
            time.sleep(.01)
        assert state['status'] == 'succeeded'
        assert state['result']['success'] is True
        assert state['result']['date'] == '2026-09-14'
    finally:
        release.set()


def test_daily_blocked_result_is_failed_job(monkeypatch):
    from api.routers import recap
    monkeypatch.setattr(recap.daily_update, 'run_daily', lambda **k: {'blocked': True, 'error': 'untrusted'})
    client = TestClient(app)
    job = client.post('/api/recap/run').json()
    for _ in range(100):
        state = client.get('/api/recap/jobs/' + job['job_id']).json()
        if state['status'] in ('succeeded', 'failed'): break
        time.sleep(.01)
    assert state['status'] == 'failed'
    assert state['error'] == 'untrusted'
    assert state['result']['success'] is False
    assert client.get('/api/recap/jobs/no-such-job').status_code == 404


def test_signal_refresh_runs_local_history_generation_without_notifications(monkeypatch):
    from api.routers import recap
    calls = []
    monkeypatch.setattr(recap.daily_update, 'run_daily', lambda **kwargs:
                        (calls.append(kwargs) or {'last_date': '2026-10-09', 'rec_rows': 294}))
    client = TestClient(app)
    response = client.post('/api/signals/refresh')
    assert response.status_code == 200
    job = response.json()
    for _ in range(100):
        state = client.get('/api/recap/jobs/' + job['job_id']).json()
        if state['status'] in ('succeeded', 'failed'):
            break
        time.sleep(.01)
    assert state['status'] == 'succeeded'
    assert calls[0]['fast_mode'] is True
    assert calls[0]['send_notification'] is False
