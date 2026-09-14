import pandas as pd

from blend import weights
from config.settings import EngineSettings


def test_adjust_formula():
    w = weights.adjusted_weights(er_a=0.10, er_b=0.02, w_old=0.5, w_min=0.2, w_max=0.8)
    expected = round(0.5 * 0.5 + 0.5 * (0.10 / 0.12), 4)
    assert abs(w[0] - expected) < 1e-6
    assert abs(w[0] + w[1] - 1.0) < 1e-6


def test_adjust_clips_and_both_negative():
    assert weights.adjusted_weights(-0.1, -0.2, 0.1, 0.2, 0.8)[0] == 0.3
    both = weights.adjusted_weights(-0.1, -0.2, 0.5, 0.2, 0.8)
    assert abs(both[0] - 0.5) < 1e-6


def test_merge_scores():
    df = pd.DataFrame({"code": ["1"], "date": ["2024-01-01"], "score_a": [80.0], "score_b": [60.0]})
    out = weights.merge_scores(df, 0.5)
    assert out.iloc[0]["score_blend"] == 70.0


def test_weight_history(tmp_path):
    path = str(tmp_path / "w.db")
    weights.init_weight_table(path)
    weights.record_weights(path, 0.6, 0.10, 0.02)
    hist = weights.load_weight_history(path)
    assert len(hist) == 1
    assert hist.iloc[0]["w_a"] == 0.6


def test_manual_override():
    es = EngineSettings(manual_override=True, manual_w_a=0.7)
    assert weights.current_weights(es, "")[0] == 0.7
