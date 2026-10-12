import pandas as pd
import pytest
from fastapi.testclient import TestClient
from config.settings import Settings, AccountSettings
from data import db
from strategy import recap, paper
from api.main import app


def recs(count=1, amount=20000):
    return pd.DataFrame([dict(code=f'{i+1:06}', name='测试', price=10., score_blend=85., suggested_weight=.2, suggested_amount=amount) for i in range(count)])


def test_real_cost_field_triggers_stop_with_quote():
    pos = pd.DataFrame([dict(code='000001', name='测试', shares=1000, cost=10.)])
    quotes = recs()
    quotes['price'] = 9.4
    quotes['suggested_amount'] = 0
    orders = recap.generate_daily_orders(quotes, pos, Settings())
    assert list(orders.action) == ['清仓']
    assert orders.iloc[0].price_ref == 9.4


@pytest.mark.parametrize('shares, expected', [(2000, 0), (1000, 900)])
def test_target_minus_existing(shares, expected):
    pos = pd.DataFrame([dict(code='000001', name='测试', shares=shares, cost=10., price=10.)])
    orders = recap.generate_daily_orders(recs(), pos, Settings())
    assert orders.shares.sum() == expected


def test_daily_orders_reserve_cash_at_limit():
    s = Settings(account=AccountSettings(cash=2500))
    orders = recap.generate_daily_orders(recs(3, 2000), settings=s)
    assert (orders.shares * orders.limit_high).sum() <= 2500


def test_no_price_cannot_invent_sell():
    pos = pd.DataFrame([dict(code='000001', shares=1000, cost=10.)])
    assert recap.generate_daily_orders(None, pos, Settings()).empty


def defense():
    db.save_macro_score('data/market.db', '2026-10-09', -.5, 'DEFENSE', -.5, -.5, -.5, '{}')


@pytest.mark.parametrize('route', ['/api/paper/order', '/api/holdings/paper'])
def test_defense_manual_buy_rejected_without_mutation(route):
    defense()
    client = TestClient(app)
    res = client.post(route, json=dict(code='000001', shares=100, price=10., side='买入'))
    assert res.status_code == 400
    assert paper.get_paper_account('artifacts/paper.db')['positions'] == []


def test_defense_ai_and_random_zero_buy(tmp_path):
    defense()
    path = str(tmp_path / 'p.db')
    recs(3).to_csv('recommendations.csv', index=False)
    assert paper.generate_ai_trading_plan(path, recommendation_path='recommendations.csv')['recommendations'] == []
    result = paper.execute_ai_trading_plan(path, recommendations=[dict(code='000001', recommended_shares=100, optimal_entry=10.)])
    assert result['executed_count'] == 0
    with pytest.raises(ValueError):
        paper.generate_random_portfolio(path)
    assert paper.get_paper_account(path)['positions'] == []


def test_ai_plan_total_budget_and_ladder(tmp_path):
    path = str(tmp_path / 'p.db')
    paper.reset_paper_account(path, 2500)
    recs(8).to_csv('recommendations.csv', index=False)
    plan = paper.generate_ai_trading_plan(path, recommendation_path='recommendations.csv')
    rows = plan['recommendations']
    assert sum(r['estimated_amount'] for r in rows) <= 2500
    for r in rows:
        assert sum(t['shares'] for t in r['ladder']) == r['recommended_shares']


def test_attribution_does_not_invent_factors():
    from services.recommendations import _build_attribution
    from config.settings import load_settings
    result = _build_attribution(load_settings(), '000001', '测试', 10, 85, 80, 90)
    assert result['factors'] == {}
    assert '主力资金持续流入' not in result['rationale']
    assert result['expected_return'] != '+15.0%'


def test_defense_cached_orders_and_export():
    from tests.test_paper_api import _seed_current_recommendations
    _seed_current_recommendations()
    defense()
    pd.DataFrame([dict(code='000001', action='买入'), dict(code='000002', action='清仓')]).to_csv('artifacts/latest_orders.csv', index=False)
    client = TestClient(app)
    assert [r['action'] for r in client.get('/api/orders').json()] == ['清仓']
    assert '买入' not in client.get('/api/orders/export').text


def test_defense_historical_sandbox_no_buy():
    from strategy.sandbox import HistoricalReplaySandbox
    defense()
    sb = HistoricalReplaySandbox(start_date='2026-10-09', end_date='2026-10-09')
    sb.trading_days = ['2026-10-09']
    sb._get_prices_at = lambda day: {'000001': 10.}
    state = sb.step()
    assert state['positions'] == []
    assert state['trades'] == []


def test_defense_holdings_diagnosis_never_adds():
    from strategy import holdings
    defense()
    holdings.save_positions('data/market.db', pd.DataFrame([dict(code='000001', name='测试', cost=10., shares=100)]))
    result = holdings.diagnose_positions('data/market.db', recs(), recs(), Settings())
    assert result.iloc[0]['action'] != '加仓'


def test_ai_existing_target_not_added(tmp_path):
    path = str(tmp_path / 'p.db')
    paper.reset_paper_account(path, 100000)
    paper.execute_paper_order(path, '000001', '测试', 'BUY', 2000, 10)
    recs().to_csv('recommendations.csv', index=False)
    assert paper.generate_ai_trading_plan(path, recommendation_path='recommendations.csv')['recommendations'] == []


def test_recap_unknown_performance_not_fabricated(tmp_path):
    md = recap.generate_daily_recap('2026-10-09', {}, {}, {}, pd.DataFrame(), save_dir=str(tmp_path))
    assert '55%' not in md
    assert '无异常跳空' not in md
    assert '整体回撤受控' not in md
    assert '未核实' in md


def test_ai_execution_rechecks_existing_target(tmp_path):
    path = str(tmp_path / 'p.db')
    paper.reset_paper_account(path, 100000)
    paper.execute_paper_order(path, '000001', '测试', 'BUY', 2000, 10)
    recs().to_csv('recommendations.csv', index=False)
    result = paper.execute_ai_trading_plan(path, recommendations=[dict(code='000001', recommended_shares=100, optimal_entry=10)], recommendation_path='recommendations.csv')
    assert result['executed_count'] == 0
    assert paper.get_paper_account(path)['positions'][0]['shares'] == 2000


def test_defense_sell_still_allowed(tmp_path):
    path = str(tmp_path / 'p.db')
    paper.execute_paper_order(path, '000001', '测试', 'BUY', 100, 10)
    defense()
    assert paper.execute_paper_order(path, '000001', '测试', 'SELL', 100, 9.4)['status'] == 'success'


def test_holdings_missing_quote_not_actionable():
    from strategy import holdings
    holdings.save_positions('data/market.db', pd.DataFrame([dict(code='000001', name='测试', cost=10., shares=100)]))
    result = holdings.diagnose_positions('data/market.db', pd.DataFrame(columns=['code', 'price']), recs(), Settings())
    assert result.iloc[0]['action'] == '数据不足'


def test_plan_target_uses_total_equity():
    from strategy import sizing
    pos = pd.DataFrame([dict(code='000001', name='测试', cost=10., shares=2000, price=10.)])
    plan = sizing.compute_plan(recs(), .02, Settings(), current_holdings=pos)
    assert plan.iloc[0]['suggested_amount'] == 24000


def test_daily_cash_budget_is_cumulative_not_per_order():
    from types import SimpleNamespace
    from config.settings import RiskSettings
    s = Settings(account=AccountSettings(cash=2500), risk=RiskSettings(max_single_position=1, max_total_position=1))
    policy = SimpleNamespace(stance='attack', allow_new_buy=True, max_total_position=1)
    orders = recap.generate_daily_orders(recs(3, 2000), settings=s, macro_res=policy)
    assert orders.shares.sum() == 200
    assert (orders.shares * orders.limit_high).sum() == pytest.approx(2010)


def test_blank_buyable_csv_does_not_suppress_orders():
    rows = recs()
    rows['buyable'] = float('nan')
    assert not recap.generate_daily_orders(rows, settings=Settings()).empty


def test_historical_entry_requires_trusted_price_history():
    from strategy.sandbox import HistoricalReplaySandbox
    defense()
    sb = HistoricalReplaySandbox(start_date='2023-01-03', end_date='2023-01-03')
    sb.trading_days = ['2023-01-03']
    sb._get_prices_at = lambda day: {'000001': 10.}
    assert sb.step()['trades'] == []


def test_ai_revalues_all_holdings_before_total_cap(tmp_path):
    path = str(tmp_path / 'p.db')
    paper.reset_paper_account(path, 100000)
    paper.execute_paper_order(path, '000001', '测试', 'BUY', 2000, 10)
    rows = recs(2)
    rows.loc[0, 'price'] = 50
    rows.to_csv('recommendations.csv', index=False)
    plan = paper.generate_ai_trading_plan(path, recommendation_path='recommendations.csv')
    assert plan['total_equity'] == 180000
    assert sum(r['estimated_amount'] for r in plan['recommendations']) <= 8000


def test_ai_price_fall_uses_current_holding_value(tmp_path):
    path = str(tmp_path / 'p.db')
    paper.reset_paper_account(path, 100000)
    paper.execute_paper_order(path, '000001', '测试', 'BUY', 2000, 10)
    rows = recs()
    rows['price'] = 5
    rows.to_csv('recommendations.csv', index=False)
    plan = paper.generate_ai_trading_plan(path, recommendation_path='recommendations.csv')
    assert plan['total_equity'] == 90000
    assert plan['recommendations'][0]['recommended_shares'] == 1600


def test_ai_missing_held_quote_does_not_spend_budget(tmp_path):
    path = str(tmp_path / 'p.db')
    paper.reset_paper_account(path, 100000)
    paper.execute_paper_order(path, '600001', '缺少报价', 'BUY', 2000, 10)
    recs().to_csv('recommendations.csv', index=False)
    assert paper.generate_ai_trading_plan(path, recommendation_path='recommendations.csv')['recommendations'] == []


def test_diagnosis_reached_recommended_target_does_not_add():
    from strategy import holdings
    holdings.save_positions('data/market.db', pd.DataFrame([dict(code='000001', name='测试', cost=10., shares=2000)]))
    rows = recs()
    rows['suggested_weight'] = .1
    result = holdings.diagnose_positions('data/market.db', rows, rows, Settings())
    assert result.iloc[0]['action'] == '持有'
