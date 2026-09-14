import datetime as dt
import pandas as pd
import pytest

from strategy import macro


def _mock_index(trend="up", days=100):
    dates = pd.bdate_range("2026-01-01", periods=days)
    if trend == "up":
        close = [100.0 + i * 1.0 for i in range(days)]  # Strong steady uptrend
    elif trend == "down":
        close = [200.0 - i * 1.0 for i in range(days)]  # Strong steady downtrend
    else:
        close = [100.0] * days
    return pd.DataFrame({"code": "sh000300", "date": dates.date, "close": close})


def test_event_calendar_third_friday():
    # 2026-09-18 is the 3rd Friday of September 2026
    fri = dt.date(2026, 9, 18)
    thu = dt.date(2026, 9, 17)
    wed = dt.date(2026, 9, 16)
    mon = dt.date(2026, 9, 14)

    events_cfg = {
        "events": [{"name": "期指交割", "type": "recurring_monthly_third_friday"}],
        "custom_dates": [],
    }

    # Wed, Thu, Fri are in the window (2 days prior to day of)
    assert macro.is_near_event(fri, events_cfg)[0] is True
    assert macro.is_near_event(thu, events_cfg)[0] is True
    assert macro.is_near_event(wed, events_cfg)[0] is True
    # Mon is > 2 days before
    assert macro.is_near_event(mon, events_cfg)[0] is False


def test_macro_score_attack():
    idx_up = _mock_index("up")
    spot = pd.DataFrame({"code": ["000001"] * 100, "pct_change": [2.0] * 90 + [-1.0] * 10})
    res = macro.compute_macro_score(
        {"sh000300": idx_up},
        spot_df=spot,
        current_date=dt.date(2026, 9, 10),
        events_cfg={"events": [], "custom_dates": []},
    )
    assert res.score >= 0.3
    assert res.stance == "attack"
    assert res.max_total_position == 0.80
    assert res.allow_new_buy is True


def test_macro_score_defense_and_event_penalty():
    idx_down = _mock_index("down")
    spot = pd.DataFrame({"code": ["000001"] * 100, "pct_change": [-3.0] * 90 + [1.0] * 10})
    res = macro.compute_macro_score(
        {"sh000300": idx_down},
        spot_df=spot,
        current_date=dt.date(2026, 9, 10),
        events_cfg={"events": [], "custom_dates": []},
    )
    assert res.score <= -0.3
    assert res.stance == "defense"
    assert res.max_total_position == 0.30
    assert res.allow_new_buy is False

    # Now test attack with event -> drops to 60%
    idx_up = _mock_index("up")
    spot_up = pd.DataFrame({"code": ["000001"] * 100, "pct_change": [2.0] * 90 + [-1.0] * 10})
    events_cfg = {"events": [], "custom_dates": [{"date": "2026-09-10", "name": "重要会议"}]}
    res_event = macro.compute_macro_score(
        {"sh000300": idx_up},
        spot_df=spot_up,
        current_date=dt.date(2026, 9, 10),
        events_cfg=events_cfg,
    )
    assert res_event.stance == "attack"
    assert res_event.event_penalty_applied is True
    assert res_event.max_total_position == 0.60
