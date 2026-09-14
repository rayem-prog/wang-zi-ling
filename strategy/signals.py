"""信号合成：评级、买卖限制标记、推荐表。"""

from __future__ import annotations

import pandas as pd


def rating(score: float) -> str:
    if score >= 80:
        return "强烈看多"
    if score >= 60:
        return "看多"
    if score >= 40:
        return "中性"
    if score >= 20:
        return "看空"
    return "强烈看空"


def buyable(pct_change: float) -> str:
    return "涨停无法买入" if pct_change >= 9.8 else ""


def build_recommendations(scores: pd.DataFrame, spot: pd.DataFrame) -> pd.DataFrame:
    df = scores.merge(spot[["code", "name", "price", "pct_change"]], on="code", how="left")
    df["rating"] = df["score_blend"].apply(rating)
    df["buyable"] = df["pct_change"].fillna(0.0).apply(buyable)
    cols = ["code", "name", "price", "score_a", "score_b", "score_blend", "rating", "buyable"]
    if "vol_20" in df.columns:
        cols.append("vol_20")
    return df[cols]
