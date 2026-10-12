"""Selected results and personal watchlists survive the service extraction."""
from dataclasses import replace
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from config.settings import Settings


@pytest.fixture
def signal_settings(tmp_path, monkeypatch):
    from data import health
    from api.health_cache import health_cache
    monkeypatch.setattr(health_cache, 'get_background', lambda settings: {})
    monkeypatch.setattr(health, 'assess_data_health', lambda settings: {})
    monkeypatch.setattr(health, 'recommendation_state', lambda settings, state: {'ready': True})
    settings = Settings()
    settings = replace(settings, data=replace(settings.data, db_path=str(tmp_path/'watch.db')),
                       paths=replace(settings.paths, artifact_dir=str(tmp_path)))
    pd.DataFrame([
        dict(code='000001', name='Low', price=20, score_blend=70, score_a=60, score_b=80),
        dict(code='000002', name='Mid', price=50, score_blend=90, score_a=95, score_b=85),
        dict(code='000003', name='High', price=100, score_blend=80, score_a=75, score_b=85),
        dict(code='000004', name='Top', price=101, score_blend=60),
        dict(code='000005', name='Invalid', price=0, score_blend=99),
    ]).to_csv(tmp_path/'recommendations.csv', index=False)
    return settings


def test_filter_sort_preserves_codes_prices_and_bracket_boundaries(signal_settings):
    from services.signals import list_signals
    rows = list_signals(signal_settings)
    assert [r['code'] for r in rows] == ['000002', '000003', '000001', '000004']
    assert [r['price_bracket'] for r in rows] == ['mid', 'high', 'low', 'top']
    filtered = list_signals(signal_settings, min_price=20, max_price=100,
                            codes='000003, 000001,', sort_by='price_asc')
    assert [r['code'] for r in filtered] == ['000001', '000003']
    assert [r['hand_cost'] for r in filtered] == [2000, 10000]
    assert list_signals(signal_settings, bracket='mid')[0]['code'] == '000002'


@pytest.mark.parametrize('sort_by, expected', [
    ('price_desc', ['000004','000003','000002','000001']),
    ('hand_cost_asc', ['000001','000002','000003','000004']),
    ('unknown', ['000002','000003','000001','000004']),
])
def test_sort_contract(signal_settings, sort_by, expected):
    from services.signals import list_signals
    assert [r['code'] for r in list_signals(signal_settings, sort_by=sort_by)] == expected


def test_stats_report_actual_top_stock_and_empty_brackets(signal_settings):
    from services.signals import signal_stats
    stats = signal_stats(signal_settings)
    assert stats['total_count'] == 4
    assert stats['brackets']['mid']['count'] == 1
    assert stats['brackets']['mid']['top_stock'] == 'Mid (¥50.0)'
    assert stats['brackets']['low']['min_hand_cost'] == 2000


def test_repository_persists_replace_and_remove_without_other_table_changes(signal_settings):
    import sqlite3
    from data.watchlist import WatchlistRepository
    with sqlite3.connect(signal_settings.data.db_path) as conn:
        conn.execute('CREATE TABLE unrelated (value TEXT)')
        conn.execute("INSERT INTO unrelated VALUES ('keep')")
    repo = WatchlistRepository(signal_settings.data.db_path)
    repo.add('000001', 'old')
    repo.add('000001', 'new')
    rows = WatchlistRepository(signal_settings.data.db_path).list()
    assert len(rows) == 1
    assert rows[0]['code'] == '000001'
    assert rows[0]['name'] == 'new'
    assert rows[0]['added_at']
    repo.remove('000001')
    assert repo.list() == []
    with sqlite3.connect(signal_settings.data.db_path) as conn:
        assert conn.execute('SELECT value FROM unrelated').fetchone() == ('keep',)


def test_unavailable_watchlist_retains_personal_name_without_fake_price(signal_settings):
    from services.signals import add_to_watchlist, list_watchlist, remove_from_watchlist
    assert add_to_watchlist(signal_settings, {'code': ' 000999 ', 'name': ' Personal '})['status'] == 'success'
    rows = list_watchlist(signal_settings)
    assert rows[0]['code'] == '000999'
    assert rows[0]['name'] == 'Personal'
    assert rows[0]['unavailable'] is True
    assert rows[0]['price'] is None
    assert rows[0]['is_watchlist'] is True
    assert add_to_watchlist(signal_settings, {'code': ' '})['status'] == 'error'
    assert len(list_watchlist(signal_settings)) == 1
    remove_from_watchlist(signal_settings, '000999')
    assert list_watchlist(signal_settings) == []


def test_health_gate_blocks_scores_but_retains_watchlist(signal_settings, monkeypatch):
    from data import health
    from services.signals import add_to_watchlist, list_signals, list_watchlist, signal_stats
    add_to_watchlist(signal_settings, {'code': '000001', 'name': 'Low'})
    monkeypatch.setattr(health, 'recommendation_state', lambda *args: {'ready': False})
    assert list_signals(signal_settings) == []
    assert list_watchlist(signal_settings)[0]['price'] is None
    stats = signal_stats(signal_settings)
    assert stats['total_count'] == 0
    assert all(b['min_hand_cost'] == 0 for b in stats['brackets'].values())


def test_http_contract_uses_the_same_service_and_private_database(signal_settings, monkeypatch):
    from api.main import app
    from api.routers import signals
    monkeypatch.setattr(signals, 'load_settings', lambda: signal_settings)
    client = TestClient(app)
    response = client.get('/api/signals?bracket=mid&sort_by=price_asc')
    assert response.status_code == 200
    assert [r['code'] for r in response.json()] == ['000002']
    assert client.post('/api/signals/watchlist', json={'code':'000002','name':'Mid'}).json()['status'] == 'success'
    assert client.get('/api/signals/watchlist').json()[0]['score_blend'] == 90
    assert client.get('/api/signals/stats').json()['total_count'] == 4
    assert client.delete('/api/signals/watchlist/000002').json()['status'] == 'success'
    assert client.get('/api/signals/watchlist').json() == []


def test_stats_uses_one_readiness_snapshot(signal_settings, monkeypatch):
    from api.health_cache import health_cache
    from services.signals import signal_stats
    calls = []
    def snapshot(settings):
        calls.append(settings)
        return {'health_cache_stale': len(calls) == 1}
    monkeypatch.setattr(health_cache, 'get_background', snapshot)
    stats = signal_stats(signal_settings)
    assert len(calls) == 1
    assert stats['total_count'] == 4
    assert stats['readiness']['audit_pending'] is True
