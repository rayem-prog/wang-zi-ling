import pandas as pd

from strategy import rules


def _row(**kw):
    base = {
        "code": "000001", "date": "2024-01-01",
        "ma5_gt_ma20": 1, "ma20_gt_ma60": 1,
        "ret_5": 0.02, "ret_20": 0.05,
        "vol_ratio_5": 1.2, "rsi_14": 55.0, "boll_pos": 0.5,
    }
    base.update(kw)
    return base


def test_score_bounds():
    df = pd.DataFrame([_row(), _row(ma5_gt_ma20=0, ma20_gt_ma60=0, ret_5=-0.03, ret_20=-0.05)])
    out = rules.score_engine_b(df)
    assert out["score_b"].between(0, 100).all()
    assert list(out.columns) == ["code", "date", "score_b"]


def test_strong_trend_scores_higher():
    good = rules.score_engine_b(pd.DataFrame([_row()]))
    bad = rules.score_engine_b(
        pd.DataFrame([_row(ma5_gt_ma20=0, ma20_gt_ma60=0, ret_5=-0.03, ret_20=-0.05, vol_ratio_5=0.5, rsi_14=80.0, boll_pos=0.95)])
    )
    assert good.iloc[0]["score_b"] > bad.iloc[0]["score_b"]
