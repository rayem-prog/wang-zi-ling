import datetime as dt

import pandas as pd
from config.settings import Settings
from data import db


def publish(path, code, date='2026-10-09', adjustment='none'):
    db.init_db(str(path))
    bars = pd.DataFrame([dict(code=code, date=date, open=11, high=12, low=10,
                              close=12, volume=100, amount=1200)])
    db.publish_bar_snapshot(str(path), code, bars, source='test', adjustment=adjustment,
                            adjustment_version='v1', evidence='test://close', reason='fixture')


def test_weekend_verified_close_values_holding_without_spot(monkeypatch):
    from services import holding_valuation as v
    settings = Settings()
    path = v.cache_path(settings, '2026-10-09')
    publish(path, '000001')
    positions = pd.DataFrame([dict(code='000001')])
    quotes, states, pending = v.valuation_quotes(settings, positions, pd.DataFrame(),
        now=dt.datetime(2026, 10, 11, 20), refresh=False)
    assert quotes.iloc[0].price == 12
    assert states['000001']['price_basis'] == 'close'
    assert states['000001']['trade_date'] == '2026-10-09'
    assert not states['000001']['stale']
    assert not pending


def test_intraday_does_not_use_old_close_for_stale_live_quote():
    from services import holding_valuation as v
    s = Settings()
    publish(v.cache_path(s, '2026-10-09'), '000001')
    quotes, states, _ = v.valuation_quotes(s, pd.DataFrame([dict(code='000001')]),
        pd.DataFrame(), now=dt.datetime(2026, 10, 12, 10), refresh=False)
    assert quotes.empty
    assert states['000001']['stale']


def test_wrong_date_or_adjusted_close_never_values_holding():
    from services import holding_valuation as v
    s = Settings()
    path = v.cache_path(s, '2026-10-09')
    publish(path, '000001', date='2026-10-08')
    publish(path, '000002', adjustment='qfq')
    quotes, states, _ = v.valuation_quotes(s, pd.DataFrame([dict(code='000001'),dict(code='000002')]),
        pd.DataFrame(), now=dt.datetime(2026, 10, 11, 20), refresh=False)
    assert quotes.empty
    assert all(s['stale'] for s in states.values())


def test_refresh_fetches_raw_closes_without_overwriting_training(monkeypatch):
    from services import holding_valuation as v
    s = Settings()
    publish(s.data.db_path, '000001', adjustment='qfq')
    def fetch(code, start, end, adjust):
        assert start == end == '2026-10-09'
        assert adjust == ''
        frame = pd.DataFrame([dict(code=code,date=start,open=11,high=12,low=10,close=12,volume=100,amount=1200)])
        frame.attrs.update(source='test',adjustment='none',adjustment_version='raw-v1',evidence='test://close')
        return frame
    monkeypatch.setattr(v.fetcher, 'fetch_daily', fetch)
    v.refresh_closes(s, ['000001'], '2026-10-09')
    assert len(db.load_training_bars(s.data.db_path, '000001', adjustment='qfq')) == 1
    assert len(db.load_training_bars(str(v.cache_path(s, '2026-10-09')), '000001', adjustment='none')) == 1


def test_holdings_endpoint_returns_dated_pnl_and_specific_missing_reason(monkeypatch):
    from services import holding_valuation as v
    from api.routers import holdings as route
    from strategy import holdings
    s = Settings()
    publish(v.cache_path(s, '2026-10-09'), '000001')
    holdings.save_positions(s.data.db_path, pd.DataFrame([
        dict(code='000001',name='valued',shares=100,cost=10),
        dict(code='000002',name='missing',shares=100,cost=10)]))
    now = dt.datetime(2026,10,11,20,tzinfo=v.exchange_now().tzinfo)
    monkeypatch.setattr(v, 'exchange_now', lambda value=None: now)
    monkeypatch.setattr(route.health_cache, 'get', lambda _: {})
    monkeypatch.setattr(route, 'recommendation_state', lambda *_: {'ready':False,'reason':'fixture'})
    monkeypatch.setattr(route, 'valuation_quotes', lambda settings,positions,spot:
                        v.valuation_quotes(settings,positions,spot,refresh=False))
    result = route.get_holdings_and_diagnostics()
    rows = {r['code']:r for r in result['diagnostics']}
    assert rows['000001']['pnl'] == 200
    assert rows['000001']['price_basis'] == 'close'
    assert rows['000001']['current_weight'] is None
    assert rows['000002']['pnl'] is None
    assert result['stale_codes'] == ['000002']
    assert '2026-10-09' in result['valuation_message']
    assert '000002' in result['warnings'][0]
