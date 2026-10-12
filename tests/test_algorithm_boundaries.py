"""Deterministic regressions for factor boundaries and unavailable evaluation evidence."""
from dataclasses import replace
import sqlite3

import pandas as pd
import pytest

from config.settings import Settings
from data import db
from features.alpha import _rsi, compute_features
from strategy import decision, rules, sandbox


@pytest.mark.parametrize('prices,want', [(range(1, 31), 100.), (range(30, 0, -1), 0.), ([10.] * 30, 50.)])
def test_rsi_unidirectional_boundaries_preserve_warmup(prices, want):
    values = _rsi(pd.Series(prices, dtype=float))
    assert (values.iloc[:14] == 50.).all()
    assert (values.iloc[-5:] == want).all()


def rule_row(**changes):
    row = dict(ma5_gt_ma20=1, ma20_gt_ma60=1, ret_5=.02, ret_20=.05,
               vol_ratio_5=0., rsi_14=55., boll_pos=.5)
    row.update(changes)
    return row


@pytest.mark.parametrize('delta,want', [(0., 100.), (.1, 100.), (-.3, 90.), (1.1, 97.)])
def test_volume_delta_converted_to_multiple_before_rules(delta, want):
    assert rules._score_row(rule_row(vol_ratio_5=delta)) == want


def test_feature_volume_ratio_remains_relative_increment():
    bars = pd.DataFrame(dict(date=pd.date_range('2024-01-01', periods=61), close=10.,
                             high=11., low=9., volume=100., amount=1000.))
    assert compute_features(bars).vol_ratio_5.iloc[-1] == 0.


@pytest.mark.parametrize('field', ['rsi_14', 'boll_pos'])
def test_valid_zero_matches_neighboring_positive_value(field):
    assert rules._score_row(rule_row(ret_20=-.05, **{field: 0.})) == rules._score_row(rule_row(ret_20=-.05, **{field: 1e-9}))


@pytest.mark.parametrize('field,default', [('vol_ratio_5', 0.), ('rsi_14', 50.), ('boll_pos', .5),
                                           ('ret_5', 0.), ('ret_20', 0.), ('ma5_gt_ma20', 0.), ('ma20_gt_ma60', 0.)])
@pytest.mark.parametrize('missing', [None, float('nan'), float('inf'), -float('inf'), pd.NA])
def test_missing_or_nonfinite_rule_inputs_use_explicit_defaults(field, default, missing):
    assert rules._score_row(rule_row(**{field: missing})) == rules._score_row(rule_row(**{field: default}))


@pytest.mark.parametrize('stance,ma_weight,rsi_penalty', [('attack', 20., 6.), ('bull', 20., 6.),
                                                        ('neutral', 15., 12.), ('defense', 8., 16.), ('bear', 8., 16.)])
def test_macro_stance_selects_expected_formula(stance, ma_weight, rsi_penalty):
    cfg, _ = rules.optimize_rules_formula(stance)
    assert cfg.w_trend_ma5 == ma_weight
    assert cfg.pen_rsi_overbought == rsi_penalty


def test_a_empty_day_with_trainable_history_returns_explicit_empty_scores():
    from tests.test_consistent_evaluation import market
    stock, bench = market(80)
    day = bench.date.iloc[-1]
    _, trained = decision.engine_a(stock, bench, day, min_train_rows=100)
    assert trained.get('model_digest')
    missing = stock[stock.date != day]
    scores, audit = decision.engine_a(missing, bench, day, min_train_rows=100)
    assert scores.empty
    assert list(scores.columns) == ['code', 'date', 'score_a']
    assert audit['reason'] == 'no_current_stock_rows'
    assert audit['model_trained'] is False
    result = decision.decide(missing, bench, day, Settings(), .5, min_train_rows=100)
    assert result.scores.empty and result.plan.empty and result.orders.empty
    assert result.model_audit['reason'] == 'no_current_stock_rows'


def test_pure_b_remains_available_when_a_cannot_run(monkeypatch):
    from tests.test_consistent_evaluation import market
    stock, bench = market()
    def unavailable_a(*args, **kwargs):
        raise RuntimeError('A training unavailable')
    monkeypatch.setattr(decision, 'engine_a', unavailable_a)
    result = decision.decide(stock, bench, bench.date.iloc[-1], Settings(), .5, engine='b')
    assert len(result.scores) == 3
    assert result.scores.score_blend.equals(result.scores.score_b)
    assert result.model_audit['reason'] == 'pure_b_engine'
    assert result.model_audit['model_trained'] is False


def isolated_sandbox(monkeypatch, tmp_path):
    settings = replace(Settings(), data=replace(Settings().data, db_path=str(tmp_path / 'sandbox.db')))
    db.init_db(settings.data.db_path)
    monkeypatch.setattr(sandbox, 'load_settings', lambda: settings)
    return settings


def test_raw_history_cannot_make_seed_ready(monkeypatch, tmp_path):
    settings = isolated_sandbox(monkeypatch, tmp_path)
    dates = pd.bdate_range('2023-01-03', periods=600).date
    bars = pd.DataFrame(dict(date=dates, open=10., high=11., low=9., close=10., volume=100., amount=1000.))
    for code in ['000001', 'sh000300']:
        db.save_bars(settings.data.db_path, code, bars)
    with sqlite3.connect(settings.data.db_path) as conn:
        before = conn.execute('SELECT count(*) FROM bars').fetchone()[0]
    result = sandbox.seed_historical_market_data(db_path=settings.data.db_path)
    assert result['status'] == 'insufficient_history'
    assert result['total_bars'] == 0
    assert result['source'] == 'trusted_database_market_bars'
    with sqlite3.connect(settings.data.db_path) as conn:
        assert conn.execute('SELECT count(*) FROM bars').fetchone()[0] == before


def test_empty_trusted_sandbox_is_blocked_with_nullable_metrics(monkeypatch, tmp_path):
    isolated_sandbox(monkeypatch, tmp_path)
    state = sandbox.HistoricalReplaySandbox().step(10)
    assert state['status'] == 'blocked'
    assert state['blocked_reason'] == 'no_trusted_trading_days'
    assert state['current_step'] == 0 and state['equity_curve'] == []
    assert state['buying_allowed'] is False
    assert state['cash'] == 1_000_000.
    for key in ['total_return_pct', 'benchmark_return_pct', 'alpha_pct', 'max_drawdown_pct',
                'win_rate_pct', 'profit_loss_ratio', 'sharpe_ratio']:
        assert state['metrics'][key] is None
        assert state['metric_reasons'][key]


@pytest.mark.parametrize('pnl,want,reason', [([], None, 'no_closed_trades'), ([100.], None, 'no_losing_trades'),
                                          ([100., -25.], 4., None), ([-25.], 0., None)])
def test_profit_factor_is_dimensionless_or_explicitly_undefined(monkeypatch, tmp_path, pnl, want, reason):
    isolated_sandbox(monkeypatch, tmp_path)
    sb = sandbox.HistoricalReplaySandbox()
    sb.trading_days = ['2023-01-03']
    sb.current_idx = 1
    sb.equity_curve = [dict(date='2023-01-03', equity=1_000_000., benchmark_return_pct=0.)]
    sb.trades = [dict(side='卖出', pnl=value) for value in pnl]
    state = sb.get_state()
    assert state['metrics']['profit_loss_ratio'] == want
    assert state['metric_reasons'].get('profit_loss_ratio') == reason


@pytest.mark.parametrize('audit,want', [({'model_trained': True, 'training_as_of': '2026-01-01', 'model_digest': 'test-model'}, True),
                                      ({'model_trained': False}, False),
                                      ({'training_as_of': '2026-01-01', 'model_digest': 'legacy-model'}, True)])
def test_daily_model_trained_reflects_actual_frozen_audit(monkeypatch, tmp_path, audit, want):
    from scripts import daily_update
    from tests.test_daily_update import test_run_daily_flow
    original_decide = daily_update.decide
    original_run = daily_update.run_daily
    results = []
    def audited_decision(*args, **kwargs):
        result = original_decide(*args, **kwargs)
        result.model_audit = dict(status='controlled training audit', top_features=[], **audit)
        return result
    def capture(*args, **kwargs):
        result = original_run(*args, **kwargs)
        results.append(result)
        return result
    monkeypatch.setattr(daily_update, 'decide', audited_decision)
    monkeypatch.setattr(daily_update, 'run_daily', capture)
    test_run_daily_flow(monkeypatch, tmp_path)
    assert results[0]['model_trained'] is want


def test_completely_empty_stock_frame_has_an_empty_decision():
    from tests.test_consistent_evaluation import market
    _, bench = market()
    result = decision.decide(pd.DataFrame(), bench, bench.date.iloc[-1], Settings(), .5)
    assert result.scores.empty and result.orders.empty
    assert list(result.scores.columns) == ['code', 'date', 'close', 'vol_20', 'score_a', 'score_b', 'score_blend']
    assert result.model_audit['reason'] == 'no_current_stock_rows'


def test_seed_check_does_not_create_missing_database(tmp_path):
    path = tmp_path / 'not-created.db'
    state = sandbox.seed_historical_market_data(db_path=str(path))
    assert state['status'] == 'insufficient_history'
    assert not path.exists()


def test_comparison_without_trusted_days_reports_blocked(monkeypatch, tmp_path):
    isolated_sandbox(monkeypatch, tmp_path)
    result = sandbox.run_multi_scheme_comparison()
    assert result['status'] == 'blocked'
    assert result['blocked_reason'] == 'no_trusted_trading_days'
    assert result['benchmark_curve'] == []
    assert all(item['total_return_pct'] is None for item in result['summary'].values())


def test_trusted_benchmark_without_stock_history_cannot_run(monkeypatch, tmp_path):
    settings = isolated_sandbox(monkeypatch, tmp_path)
    bars = pd.DataFrame(dict(date=[pd.Timestamp('2023-01-03').date()],
                             open=100., high=101., low=99., close=100., volume=100., amount=10000.))
    db.publish_bar_snapshot(settings.data.db_path, settings.data.index_symbol, bars,
                            source='test_fixture', adjustment='none', adjustment_version='v1',
                            evidence='test://benchmark', reason='controlled fixture')
    state = sandbox.HistoricalReplaySandbox().get_state()
    assert state['status'] == 'blocked'
    assert state['total_steps'] == 0


def test_disjoint_trusted_stock_and_benchmark_dates_are_blocked(monkeypatch, tmp_path):
    settings = isolated_sandbox(monkeypatch, tmp_path)
    for code, day, adjustment in [('000001', '2023-01-03', 'qfq'), ('sh000300', '2023-01-04', 'none')]:
        bars = pd.DataFrame(dict(date=[pd.Timestamp(day).date()], open=10., high=11., low=9.,
                                 close=10., volume=100., amount=1000.))
        db.publish_bar_snapshot(settings.data.db_path, code, bars, source='test_fixture',
                                adjustment=adjustment, adjustment_version='v1',
                                evidence='test://disjoint', reason='controlled fixture')
    state = sandbox.HistoricalReplaySandbox().get_state()
    assert state['status'] == 'blocked'
    assert state['total_steps'] == 0


def test_empty_day_cannot_generate_orders_from_stale_held_prices():
    from tests.test_consistent_evaluation import market
    stock, bench = market()
    day = bench.date.iloc[-1]
    holdings = pd.DataFrame([dict(code='000001', name='held', shares=100, cost=8., price=10.)])
    before = holdings.copy(deep=True)
    result = decision.decide(stock[stock.date != day], bench, day, Settings(), .5, holdings)
    assert result.orders.empty
    assert list(result.orders.columns) == decision.recap.ORDER_COLUMNS
    pd.testing.assert_frame_equal(holdings, before)


def test_partial_missing_day_cannot_sell_stale_held_quote():
    from tests.test_consistent_evaluation import market
    stock, bench = market()
    day = bench.date.iloc[-1]
    positions = pd.DataFrame([dict(code='000001',name='held',shares=100,cost=8.,price=10.)])
    current = stock[~((stock.date == day) & (stock.code == '000001'))]
    result = decision.decide(current,bench,day,Settings(),.5,positions)
    assert not result.orders.code.eq('000001').any()
