"""Controlled fixtures catch leakage, mutable forecasts and duplicate rewards."""
from dataclasses import replace
import sqlite3

import pandas as pd
import pytest

from config.settings import Settings, EngineSettings


def market(days=12):
    dates = pd.bdate_range('2024-03-04', periods=days).date
    rows = []
    for code, growth in [('000001', .02), ('000002', -.01), ('000003', .0)]:
        for i, day in enumerate(dates):
            rows.append(dict(code=code, date=day, close=10*(1+growth*i), open=10*(1+growth*i),
                high=11., low=8., volume=1000., amount=10000., ret_1=.01,
                ret_5=.02, ret_20=.04, vol_20=.02, ma5_gt_ma20=1, ma20_gt_ma60=1,
                rsi_14=55., boll_pos=.5, vol_ratio_5=1.))
    return pd.DataFrame(rows), pd.DataFrame(dict(date=dates, close=100.))


def payload(day):
    return dict(date=str(day), w_a=.5, benchmark_close=100., scores=[
        dict(code='000001', score_a=95., score_b=10., close=10.),
        dict(code='000002', score_a=80., score_b=60., close=10.),
        dict(code='000003', score_a=60., score_b=95., close=10.),
    ])


def test_freeze_first_prediction_and_evaluate_only_future(tmp_path):
    from strategy.evaluation import PredictionLedger
    stock, bench = market()
    ledger = PredictionLedger(str(tmp_path/'eval.db'))
    first = payload(bench.date.iloc[0])
    ledger.freeze(first['date'], first)
    changed = payload(bench.date.iloc[0]); changed['scores'][0]['score_a'] = 0
    ledger.freeze(first['date'], changed)
    assert ledger.get(first['date'])['scores'][0]['score_a'] == 95
    before = ledger.advance(stock, bench, bench.date.iloc[4], EngineSettings())
    assert before['new_w_a'] == .5
    assert before['evaluated_count'] == 0
    after = ledger.advance(stock, bench, bench.date.iloc[5], EngineSettings())
    assert after['evaluated_count'] == 1
    # Three-stock pool selects all three: 10%, -5%, 0%; average 1.6667%.
    assert after['r_a'] == pytest.approx(100/60, abs=.001)
    assert after['r_b'] == pytest.approx(100/60, abs=.001)
    assert after['new_w_a'] == .5
    again = PredictionLedger(str(tmp_path/'eval.db')).advance(stock, bench, bench.date.iloc[5], EngineSettings())
    assert again['evaluated_count'] == 0
    with sqlite3.connect(tmp_path/'eval.db') as conn:
        assert conn.execute('SELECT count(*) FROM prediction_evaluations').fetchone()[0] == 1


def test_missing_future_quote_remains_pending_and_manual_override(tmp_path):
    from strategy.evaluation import PredictionLedger
    stock, bench = market()
    ledger = PredictionLedger(str(tmp_path/'eval.db'))
    first = payload(bench.date.iloc[0]); ledger.freeze(first['date'], first)
    missing = stock[~((stock.code == '000001') & (stock.date == bench.date.iloc[3]))]
    result = ledger.advance(missing, bench, bench.date.iloc[8], EngineSettings())
    assert result['evaluated_count'] == 0
    assert result['new_w_a'] == .5
    result = ledger.advance(stock, bench, bench.date.iloc[8], EngineSettings(manual_override=True, manual_w_a=.7))
    assert result['new_w_a'] == .7
    assert result['evaluated_count'] == 0


def test_decision_is_asof_and_row_order_invariant():
    from strategy.decision import decide
    stock, bench = market()
    date = bench.date.iloc[5]
    a = decide(stock, bench, date, Settings(), .5)
    changed = stock.copy(); changed.loc[changed.date > date, 'close'] = 99999
    changed_bench = bench.copy(); changed_bench.loc[changed_bench.date > date, 'close'] = 1
    b = decide(changed.sample(frac=1, random_state=7), changed_bench, date, Settings(), .5)
    pd.testing.assert_frame_equal(a.scores, b.scores)
    pd.testing.assert_frame_equal(a.plan, b.plan)
    pd.testing.assert_frame_equal(a.orders, b.orders)
    assert len(a.orders) > 0
    assert list(a.scores.code) == ['000001', '000002', '000003']


def test_daily_repeated_run_keeps_frozen_decision(monkeypatch, tmp_path):
    # Exercise the existing complete daily fixture, with its real temporary database.
    from tests.test_daily_update import test_run_daily_flow
    from scripts import daily_update
    original = daily_update.run_daily
    captured = []
    def repeat(*args, **kwargs):
        first = original(*args, **kwargs)
        score_file = tmp_path/'artifacts/latest_scores.csv'
        scores = pd.read_csv(score_file)
        second = original(*args, **kwargs)
        pd.testing.assert_frame_equal(scores, pd.read_csv(score_file))
        assert first['weight_a'] == second['weight_a']
        assert second['weight_eval']['evaluated_count'] == 0
        captured.append(second)
        return second
    monkeypatch.setattr(daily_update, 'run_daily', repeat)
    test_run_daily_flow(monkeypatch, tmp_path)
    assert len(captured) == 1


def test_weight_reward_occurs_once_with_different_frozen_rankings(tmp_path):
    from strategy.evaluation import PredictionLedger
    stock, bench = market()
    rows = []
    forecasts = []
    for i in range(20):
        code = f'{i+1:06d}'
        growth = .02 if i < 4 else -.01
        for j, day in enumerate(bench.date):
            rows.append(dict(code=code,date=day,close=10*(1+growth*j)))
        forecasts.append(dict(code=code,close=10.,score_a=100-i,score_b=i))
    stock = pd.DataFrame(rows)
    ledger = PredictionLedger(str(tmp_path/'w.db'))
    ledger.freeze(str(bench.date.iloc[0]),dict(scores=forecasts,benchmark_close=100.))
    audit = ledger.advance(stock,bench,bench.date.iloc[5],EngineSettings())
    assert audit['r_a'] == pytest.approx(10.)
    assert audit['r_b'] == pytest.approx(-5.)
    assert audit['new_w_a'] == .7496
    again = ledger.advance(stock,bench,bench.date.iloc[6],EngineSettings())
    assert again['new_w_a'] == .7496
    assert again['evaluated_count'] == 0


def test_sandbox_batch_equals_single_steps_and_ignores_current_macro(monkeypatch):
    from data import db
    from strategy.sandbox import HistoricalReplaySandbox
    from tests.test_sandbox import historical_bars
    historical_bars.__wrapped__()
    a = HistoricalReplaySandbox(start_date='2023-04-10', end_date='2023-04-25')
    b = HistoricalReplaySandbox(start_date='2023-04-10', end_date='2023-04-25')
    expected = a.step(12)
    assert len(expected['trades']) > 0
    # A later macro observation must not travel back into 2023 decisions.
    db.save_macro_score('data/market.db', '2026-10-09', -.5, 'DEFENSE', -.5, -.5, -.5, '{}')
    for _ in range(12):
        actual = b.step()
    assert actual['equity_curve'] == expected['equity_curve']
    assert actual['trades'] == expected['trades']


def test_sandbox_does_not_buy_at_signal_day_close():
    from strategy.sandbox import HistoricalReplaySandbox
    from tests.test_sandbox import historical_bars
    historical_bars.__wrapped__()
    sandbox = HistoricalReplaySandbox(start_date='2023-04-10', end_date='2023-04-25')
    first = sandbox.step()
    assert first['positions'] == []
    assert first['trades'] == []
    assert sandbox.step()['positions']


def test_trained_model_decisions_are_identical_across_processes(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    script = '''import json
from tests.test_consistent_evaluation import market
from strategy.decision import decide
from config.settings import Settings
s,b=market(80)
d=decide(s,b,b.date.iloc[-1],Settings(),.5,min_train_rows=100)
assert d.model_audit.get('model_digest')
print(json.dumps(d.to_payload(),sort_keys=True))
'''
    root = str(Path(__file__).resolve().parents[1])
    outputs = []
    for seed in ('1','47'):
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=root)
        outputs.append(subprocess.check_output([sys.executable,'-c',script],env=env,text=True,cwd=tmp_path))
    assert outputs[0] == outputs[1]


def test_frozen_reference_prices_survive_later_corrections(tmp_path):
    from strategy.evaluation import PredictionLedger
    stock, bench = market()
    ledger = PredictionLedger(str(tmp_path/'e.db'))
    first = payload(bench.date.iloc[0]); first['benchmark_close'] = 100.
    ledger.freeze(first['date'],first)
    stock.loc[stock.date == bench.date.iloc[0], 'close'] = 1000.
    bench.loc[bench.date == bench.date.iloc[0], 'close'] = 1000.
    audit = ledger.advance(stock,bench,bench.date.iloc[5],EngineSettings())
    assert audit['r_a'] == pytest.approx(1.6667)
    assert audit['bench_return'] == 0.


def test_legacy_same_day_weight_api_cannot_mutate_history(tmp_path):
    from blend import weights
    with pytest.raises(ValueError,match='冻结'):
        weights.evaluate_and_update_weights(pd.DataFrame([dict(code='000001',score_a=90.,score_b=50.,ret_1=.1)]),
            None,0.,.5,str(tmp_path/'legacy.db'))
    assert not (tmp_path/'legacy.db').exists()


def test_weight_summary_reads_mature_evaluations(tmp_path):
    from strategy.evaluation import PredictionLedger
    from blend import weights
    stock, bench = market()
    ledger = PredictionLedger(str(tmp_path/'e.db'))
    ledger.freeze(str(bench.date.iloc[0]),payload(bench.date.iloc[0]))
    ledger.advance(stock,bench,bench.date.iloc[5],EngineSettings())
    history = weights.load_weight_history(str(tmp_path/'e.db'))
    assert len(history) == 1
    assert history.iloc[0]['basis'] == 'frozen_future_5_sessions'
    assert weights.current_weights(EngineSettings(w_a=.6,w_b=.4),str(tmp_path/'e.db')) == (.5,.5)


def test_replay_decision_equals_shared_formal_decision():
    from backtest.walkforward import BacktestConfig, run_walk_forward
    from config.settings import AccountSettings
    from strategy.decision import decide
    stock, bench = market(6)
    settings = replace(Settings(), account=AccountSettings(cash=100000.,max_positions=2))
    date = bench.date.iloc[0]
    expected = decide(stock,bench,date,settings,.5).to_payload()
    result = run_walk_forward(stock,bench,BacktestConfig(start=str(date),end=str(bench.date.iloc[-1]),top_n=2),settings=settings)
    actual = result['blend'].decisions[0]
    assert actual['scores'] == expected['scores']
    assert actual['orders'] == expected['orders']
    assert actual['macro'] == expected['macro']


def test_sandbox_freezes_event_calendar_at_initialization(monkeypatch):
    from strategy import macro
    from strategy.sandbox import HistoricalReplaySandbox
    from tests.test_sandbox import historical_bars
    historical_bars.__wrapped__()
    a = HistoricalReplaySandbox(start_date='2023-04-10',end_date='2023-04-25')
    b = HistoricalReplaySandbox(start_date='2023-04-10',end_date='2023-04-25')
    expected = a.step(3)
    monkeypatch.setattr(macro,'load_event_calendar',lambda:dict(events=[],custom_dates=[dict(date='2023-04-11',name='late change')]))
    actual = b.step(3)
    assert actual['trades'] == expected['trades']
    assert b.decisions == a.decisions


def test_incomplete_forecast_does_not_block_later_mature_forecast(tmp_path):
    from strategy.evaluation import PredictionLedger
    stock,bench = market(12)
    ledger = PredictionLedger(str(tmp_path/'e.db'))
    first = payload(bench.date.iloc[0]); ledger.freeze(first['date'],first)
    second = payload(bench.date.iloc[4]); ledger.freeze(second['date'],second)
    missing = stock[~((stock.code=='000001') & (stock.date==bench.date.iloc[1]))]
    engine = EngineSettings(w_a=.6,w_b=.4)
    audit = ledger.advance(missing,bench,bench.date.iloc[10],engine)
    assert audit['evaluated_count'] == 1
    assert audit['new_w_a'] == .55
    assert audit['prediction_date'] == str(bench.date.iloc[4])
    # Backfilling the old cohort later applies once, using the actual application order.
    audit = ledger.advance(stock,bench,bench.date.iloc[11],engine)
    assert audit['evaluated_count'] == 1
    assert audit['prediction_date'] == str(bench.date.iloc[0])
    assert audit['new_w_a'] == .525
    again = ledger.advance(stock,bench,bench.date.iloc[11],engine)
    assert again['new_w_a'] == audit['new_w_a']
    assert again['evaluated_count'] == 0


def test_entry_schedule_is_explicit_shared_decision_input():
    from strategy.decision import decide
    stock,bench = market()
    first = decide(stock,bench,bench.date.iloc[0],Settings(),.5)
    paused = decide(stock,bench,bench.date.iloc[0],Settings(),.5,allow_new_entries=False)
    assert not first.orders.empty
    assert paused.orders.empty
    assert paused.parameters['allow_new_entries'] is False
    pd.testing.assert_frame_equal(first.scores,paused.scores)
