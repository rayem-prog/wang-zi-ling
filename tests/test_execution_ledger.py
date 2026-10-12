"""Account values are hand computed, including blocked orders and entry fees."""
import pandas as pd
import pytest


def bars(open_=10., close=10., volume=1000., low=9., high=11.):
    return pd.DataFrame([dict(code='000001', open=open_, close=close, low=low, high=high, volume=volume)])


def order(action='买入', shares=1000):
    return pd.DataFrame([dict(code='000001', name='测试', action=action, shares=shares,
                             limit_low=9.9, limit_high=10.05, price_ref=10., date='2024-03-04')])


def test_entry_fee_and_first_loss_are_in_equity():
    from strategy.execution import Portfolio
    account = Portfolio(20000., .002)
    account.execute(order(), bars(), '2024-03-05')
    account.mark(bars(close=9.))
    assert account.cash == pytest.approx(9990.)
    assert account.equity == pytest.approx(18990.)
    assert account.audit[0]['status'] == 'filled'


@pytest.mark.parametrize('quote, status', [(bars(volume=0), 'unfilled'), (bars(open_=11,low=11,high=11), 'unfilled'), (pd.DataFrame(), 'unfilled')])
def test_unfilled_order_preserves_cash_and_audit(quote, status):
    from strategy.execution import Portfolio
    account = Portfolio(20000., .002)
    account.execute(order(), quote, '2024-03-05')
    assert account.cash == 20000.
    assert account.positions == {}
    assert account.audit[0]['status'] == status
    assert account.audit[0]['shares'] == 1000


def test_missing_quote_keeps_last_mark_instead_of_cost():
    from strategy.execution import Portfolio
    account = Portfolio(20000., 0)
    account.execute(order(), bars(), '2024-03-05')
    account.mark(bars(close=8.))
    account.mark(pd.DataFrame())
    assert account.equity == 18000.
    assert account.stale_codes == ['000001']
    assert account.holdings().iloc[0]['price'] == 8.


def test_no_same_day_execution_and_sell_fees():
    from strategy.execution import Portfolio
    account = Portfolio(20000., .002)
    with pytest.raises(ValueError):
        account.execute(order(), bars(), '2024-03-04')
    account.execute(order(), bars(), '2024-03-05')
    account.execute(order('清仓').assign(date='2024-03-05'), bars(), '2024-03-06')
    assert account.cash == pytest.approx(19980.)
    assert account.positions == {}


def test_missing_one_candidate_does_not_reassign_its_budget():
    from strategy.execution import Portfolio
    account = Portfolio(20000., 0)
    orders = pd.concat([order(shares=500), order(shares=500).assign(code='000002')])
    account.execute(orders, bars(), '2024-03-05')
    account.mark(bars(close=8.))
    assert account.cash == 15000.
    assert account.equity == 19000.
    assert account.positions['000001']['shares'] == 500


def test_daily_walkforward_captures_interim_drawdown_and_first_fees():
    from backtest.walkforward import BacktestConfig, run_walk_forward
    from tests.test_consistent_evaluation import market
    stock, bench = market(6)
    # Same score, buy at 10 on day 2, plunge to 5 before recovering to 10.
    stock['open'] = 10.; stock['close'] = 10.
    stock.loc[stock.date == bench.date.iloc[2], 'close'] = 5.
    cfg = BacktestConfig(start=str(bench.date.iloc[0]), end=str(bench.date.iloc[-1]), rebalance_days=5, top_n=2, cost_rate=.002)
    result = run_walk_forward(stock, bench, cfg)['b']
    assert len(result.equity) == 6
    assert result.equity.iloc[0] == 100000.
    assert result.equity.iloc[1] < 100000.
    assert result.stats['max_drawdown'] < -.1
    assert not result.trades.empty
    assert 'cash' in result.valuations.columns


def test_order_valid_only_on_next_actual_session():
    from strategy.execution import Portfolio
    account = Portfolio(20000.,0.)
    account.execute(order(),bars(),'2024-03-12')
    assert account.cash == 20000.
    assert account.positions == {}
    assert account.audit[0]['status'] == 'expired'


@pytest.mark.parametrize('unit, volume, expected', [('lots',1,100),('shares',150,100)])
def test_liquidity_caps_buy_and_sell_with_explicit_units(unit,volume,expected):
    from strategy.execution import Portfolio
    account = Portfolio(20000.,0.)
    quote = bars(volume=volume).assign(volume_unit=unit)
    account.execute(order(),quote,'2024-03-05')
    assert account.positions['000001']['shares'] == expected
    assert account.audit[0]['status'] == 'partial'
    account.execute(order('清仓',shares=expected).assign(date='2024-03-05'),quote,'2024-03-06')
    assert account.positions == {}
    assert account.cash == 20000.


def test_sandbox_rechecks_frozen_global_cap_after_overnight_gap(monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace
    from config.settings import Settings
    from strategy import sandbox
    from tests.test_sandbox import historical_bars
    historical_bars.__wrapped__()
    settings = replace(Settings(),risk=replace(Settings().risk,max_total_position=.2))
    monkeypatch.setattr(sandbox,'load_settings',lambda:settings)
    sb = sandbox.HistoricalReplaySandbox(start_date='2023-04-10',end_date='2023-04-11',initial_cash=100000.,
                                        scheme='custom',custom_config={'cost_rate':0.})
    sb.current_idx = 1
    sb._account.cash = 90000.
    sb._account.positions['000001'] = dict(name='held',shares=1000,avg_cost=10.,current_price=10.)
    sb._bars = pd.concat([bars(open_=12.,close=12.).assign(date=pd.Timestamp('2023-04-11').date()),
                          bars().assign(code='000002',date=pd.Timestamp('2023-04-11').date())])
    sb._pending = SimpleNamespace(orders=order(shares=900).assign(code='000002'),date='2023-04-10',
                                  macro=SimpleNamespace(max_total_position=.6),
                                  parameters={'risk':{'max_total_position':.2}})
    sb.step()
    assert sb._account.positions['000002']['shares'] == 800
    assert (sb._account.equity-sb._account.cash)/sb._account.equity <= .2
