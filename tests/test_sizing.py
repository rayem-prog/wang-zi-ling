import pandas as pd

from config.settings import Settings
from strategy import sizing


def test_target_weight_caps():
    s = Settings()
    assert sizing.target_weight(85.0, 0.02, 0.02, s) == 0.2
    assert sizing.target_weight(50.0, 0.02, 0.02, s) == 0.0


def test_volatility_scale_reduces():
    s = Settings()
    low_vol = sizing.target_weight(70.0, 0.02, 0.02, s)
    high_vol = sizing.target_weight(70.0, 0.08, 0.02, s)
    assert high_vol < low_vol


def test_stop_and_take():
    s = Settings()
    stop, take = sizing.stop_and_take(10.0, s)
    assert stop == 9.5
    assert take == 11.5


def test_compute_plan():
    s = Settings(account=Settings().account.__class__(cash=100_000.0))
    rec = pd.DataFrame(
        {"code": ["000001"], "price": [10.0], "score_blend": [85.0], "vol_20": [0.02]}
    )
    out = sizing.compute_plan(rec, median_vol=0.02, settings=s)
    assert out.iloc[0]["suggested_amount"] == 20_000.0


def test_compute_plan_macro_defense():
    from strategy.macro import MacroResult
    s = Settings(account=Settings().account.__class__(cash=100_000.0))
    rec = pd.DataFrame(
        {"code": ["000001"], "price": [10.0], "score_blend": [85.0], "vol_20": [0.02]}
    )
    macro_defense = MacroResult(
        score=-0.5,
        stance="defense",
        trend_score=-0.6,
        breadth_score=-0.4,
        vol_score=-0.2,
        max_total_position=0.30,
        allow_new_buy=False,
        event_penalty_applied=False,
    )
    out = sizing.compute_plan(rec, median_vol=0.02, settings=s, macro_res=macro_defense)
    assert out.iloc[0]["suggested_weight"] == 0.0
    assert out.iloc[0]["suggested_amount"] == 0.0

