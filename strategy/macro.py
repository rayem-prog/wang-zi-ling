"""M1 宏观温度计 + M4 事件日历：大盘趋势/宽度/波动打分与动态仓位闸门。"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


@dataclass
class MacroResult:
    score: float  # [-1.0, +1.0]
    stance: str  # "attack" | "neutral" | "defense"
    trend_score: float
    breadth_score: float
    vol_score: float
    max_total_position: float  # 0.80 / 0.60 / 0.30
    allow_new_buy: bool  # defense 时为 False
    event_penalty_applied: bool
    event_name: str = ""
    details: dict = field(default_factory=dict)


def load_event_calendar(yaml_path: str | None = None) -> dict:
    target = Path(yaml_path or "config/events.yaml")
    if not target.exists():
        return {"events": [], "custom_dates": []}
    try:
        with open(target, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {"events": [], "custom_dates": []}
    except Exception:
        return {"events": [], "custom_dates": []}


def _is_third_friday_window(target_date: dt.date) -> bool:
    """每月第三个周五及其前 2 个交易日（周三至周五）"""
    # Find third Friday of target_date's month
    first_day = dt.date(target_date.year, target_date.month, 1)
    # 4 is Friday in Python weekday() (0=Monday...4=Friday)
    first_friday = first_day + dt.timedelta(days=(4 - first_day.weekday()) % 7)
    third_friday = first_friday + dt.timedelta(weeks=2)

    window_start = third_friday - dt.timedelta(days=2)
    return window_start <= target_date <= third_friday


def is_near_event(target_date: dt.date, events_cfg: dict | None = None) -> tuple[bool, str]:
    if events_cfg is None:
        events_cfg = load_event_calendar()

    # 1. Check custom dates (within 2 days prior or day of)
    custom_dates = events_cfg.get("custom_dates", [])
    for item in custom_dates:
        d_str = item.get("date")
        if not d_str:
            continue
        try:
            ev_date = pd.Timestamp(d_str).date()
            if (ev_date - dt.timedelta(days=2)) <= target_date <= ev_date:
                return True, item.get("name", "重大事件")
        except Exception:
            continue

    # 2. Check general events
    events = events_cfg.get("events", [])
    for ev in events:
        ev_type = ev.get("type")
        if ev_type == "recurring_monthly_third_friday":
            if _is_third_friday_window(target_date):
                return True, ev.get("name", "股指期货交割周")
        elif ev_type == "date_range":
            s_part = ev.get("start")  # MM-DD
            e_part = ev.get("end")  # MM-DD
            if s_part and e_part:
                try:
                    s_month, s_day = map(int, s_part.split("-"))
                    e_month, e_day = map(int, e_part.split("-"))
                    s_date = dt.date(target_date.year, s_month, s_day)
                    e_date = dt.date(target_date.year, e_month, e_day)
                    window_start = s_date - dt.timedelta(days=2)
                    if window_start <= target_date <= e_date:
                        return True, ev.get("name", "重要时间窗口")
                except Exception:
                    continue

    return False, ""


def compute_macro_score(
    index_bars: dict[str, pd.DataFrame],
    spot_df: pd.DataFrame | None = None,
    events_cfg: dict | None = None,
    current_date: dt.date | None = None,
) -> MacroResult:
    """
    计算宏观大盘温度计得分。
    index_bars: 指数字典，包含如 'sh000300', 'sh000905', 'sz399006' 的日线 DataFrame。
    """
    if current_date is None:
        current_date = dt.date.today()

    trend_subscores = []
    vol_subscores = []
    details = {}

    # 1. 趋势维度 (3 指数 x 3 信号 = 9 个子项，均值 [-1, +1])
    for symbol, df in index_bars.items():
        if df.empty or len(df) < 60:
            continue
        df = df.sort_values("date").reset_index(drop=True)
        close = df["close"]
        c_now = float(close.iloc[-1])
        ma20 = float(close.tail(20).mean())
        ma60 = float(close.tail(60).mean())
        c_20_ago = float(close.iloc[-20])

        s_ma20 = 1.0 if c_now > ma20 else (-1.0 if c_now < ma20 else 0.0)
        s_ma60 = 1.0 if c_now > ma60 else (-1.0 if c_now < ma60 else 0.0)
        s_mom = 1.0 if c_now > c_20_ago else (-1.0 if c_now < c_20_ago else 0.0)

        trend_subscores.extend([s_ma20, s_ma60, s_mom])
        details[f"{symbol}_signals"] = {"ma20": s_ma20, "ma60": s_ma60, "mom": s_mom}

        # Volatility sub-metrics for this index
        ret_series = close.pct_change().dropna().tail(20)
        hist_ret = close.pct_change().dropna()
        if len(ret_series) >= 10:
            cur_vol = float(ret_series.std() * np.sqrt(250))
            if len(hist_ret) >= 60:
                roll_vol = hist_ret.rolling(20).std() * np.sqrt(250)
                q30 = float(roll_vol.quantile(0.3))
                q70 = float(roll_vol.quantile(0.7))
                s_vol_q = 1.0 if cur_vol < q30 else (-1.0 if cur_vol > q70 else 0.0)
            else:
                s_vol_q = 0.0

            high_60 = float(close.tail(60).max())
            dd_60 = (c_now / high_60 - 1.0) if high_60 > 0 else 0.0
            s_dd = 1.0 if dd_60 > -0.02 else (-1.0 if dd_60 < -0.05 else 0.0)
            vol_subscores.extend([s_vol_q, s_dd])

    trend_score = float(np.mean(trend_subscores)) if trend_subscores else 0.0
    vol_score = float(np.mean(vol_subscores)) if vol_subscores else 0.0

    # 2. 宽度维度 (涨跌比、涨跌停差、新高新低)
    breadth_subscores = []
    if spot_df is not None and not spot_df.empty and "pct_change" in spot_df.columns:
        pcts = spot_df["pct_change"].dropna().astype(float)
        up_count = int((pcts > 0).sum())
        down_count = int((pcts < 0).sum())
        ratio = (up_count / down_count) if down_count > 0 else 2.0
        s_ratio = 1.0 if ratio > 1.2 else (-1.0 if ratio < 0.8 else 0.0)

        limit_up = int((pcts >= 9.8).sum())
        limit_down = int((pcts <= -9.8).sum())
        diff_limit = limit_up - limit_down
        s_limit = 1.0 if diff_limit > 30 else (-1.0 if diff_limit < -30 else 0.0)

        breadth_subscores.extend([s_ratio, s_limit])
        details["breadth"] = {
            "up_count": up_count,
            "down_count": down_count,
            "ratio": round(ratio, 2),
            "limit_up": limit_up,
            "limit_down": limit_down,
        }

    breadth_score = float(np.mean(breadth_subscores)) if breadth_subscores else trend_score

    # 3. 综合加权总分: 0.5 * 趋势 + 0.3 * 宽度 + 0.2 * 波动
    raw_score = 0.5 * trend_score + 0.3 * breadth_score + 0.2 * vol_score
    score = float(np.clip(raw_score, -1.0, 1.0))

    # 4. 判定档位
    if score >= 0.3:
        stance = "attack"
        base_cap = 0.80
        allow_new_buy = True
    elif score <= -0.3:
        stance = "defense"
        base_cap = 0.30
        allow_new_buy = False
    else:
        stance = "neutral"
        base_cap = 0.60
        allow_new_buy = True

    # 5. M4 事件日历降档
    near_event, event_name = is_near_event(current_date, events_cfg)
    event_penalty_applied = False
    final_cap = base_cap

    if near_event:
        event_penalty_applied = True
        if base_cap == 0.80:
            final_cap = 0.60
        elif base_cap == 0.60:
            final_cap = 0.30
        # 30% remains 30%

    return MacroResult(
        score=round(score, 3),
        stance=stance,
        trend_score=round(trend_score, 3),
        breadth_score=round(breadth_score, 3),
        vol_score=round(vol_score, 3),
        max_total_position=final_cap,
        allow_new_buy=allow_new_buy,
        event_penalty_applied=event_penalty_applied,
        event_name=event_name,
        details=details,
    )
