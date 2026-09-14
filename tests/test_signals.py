import pandas as pd

from strategy import signals


def test_rating_thresholds():
    assert signals.rating(85) == "强烈看多"
    assert signals.rating(60) == "看多"
    assert signals.rating(50) == "中性"
    assert signals.rating(20) == "看空"
    assert signals.rating(10) == "强烈看空"


def test_buyable_flag():
    assert signals.buyable(9.8) == "涨停无法买入"
    assert signals.buyable(5.0) == ""


def test_build_recommendations():
    scores = pd.DataFrame(
        {
            "code": ["000001"], "date": ["2024-01-01"], "score_a": [80.0], "score_b": [60.0],
            "score_blend": [70.0], "vol_20": [0.02],
        }
    )
    spot = pd.DataFrame(
        {"code": ["000001"], "name": ["平安银行"], "price": [10.5], "pct_change": [1.0], "volume": [1], "amount": [1]}
    )
    out = signals.build_recommendations(scores, spot)
    assert out.iloc[0]["name"] == "平安银行"
    assert out.iloc[0]["rating"] == "看多"
    assert out.iloc[0]["buyable"] == ""
    assert out.iloc[0]["vol_20"] == 0.02
