"""个性化 A：按信号强度与波动率给出目标仓位/金额/止损/止盈。"""

from __future__ import annotations

import pandas as pd

from strategy.signals import rating
from strategy import risk


def target_weight(score: float, vol_20: float, median_vol: float, settings) -> float:
    r = rating(score)
    if r == "强烈看多":
        strength = 1.0
    elif r == "看多":
        strength = 0.6
    else:
        return 0.0
    base = settings.risk.max_single_position * strength
    vol_scale = min(1.5, max(0.5, median_vol / max(vol_20, 1e-6)))
    return round(min(base * vol_scale, settings.risk.max_single_position), 4)


def stop_and_take(price: float, settings) -> tuple[float, float]:
    return round(price * (1 - settings.risk.stop_loss), 2), round(price * (1 + settings.risk.take_profit), 2)


def compute_plan(rec: pd.DataFrame, median_vol: float, settings, macro_res=None, current_holdings=None) -> pd.DataFrame:
    out = rec.copy()
    if out.empty:
        return out
    if "vol_20" not in out.columns:
        out["vol_20"] = median_vol

    equity = max(settings.account.cash, 0)
    if current_holdings is not None and not current_holdings.empty:
        prices = {str(r["code"]).zfill(6): risk.positive(r.get("price")) for _, r in out.iterrows()}
        equity += sum(risk.positive(h.get("shares")) * (prices.get(str(h["code"]).zfill(6)) or risk.positive(h.get("price")) or risk.positive(h.get("cost")))
                      for _, h in current_holdings.iterrows())
    macro_res = macro_res or risk.current_policy(settings)
    if not risk.buying_allowed(macro_res):
        out["suggested_weight"] = 0.0
        out["suggested_amount"] = 0.0
    else:
        weights = [
            target_weight(s, v, median_vol, settings) for s, v in zip(out["score_blend"], out["vol_20"])
        ]
        max_total = settings.risk.max_total_position
        if macro_res is not None and hasattr(macro_res, "max_total_position"):
            max_total = min(max_total, macro_res.max_total_position)

        tot_w = sum(weights)
        if tot_w > max_total and tot_w > 0:
            scale = max_total / tot_w
            weights = [round(w * scale, 4) for w in weights]

        out["suggested_weight"] = weights
        out["suggested_amount"] = (out["suggested_weight"] * equity).round(0)

    stops, takes = zip(*[stop_and_take(p, settings) for p in out["price"]])
    out["stop_loss"] = stops
    out["take_profit"] = takes
    return out

