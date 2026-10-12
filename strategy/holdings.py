"""个性化 B：持仓诊断、风格分类、集中度预警。"""

from __future__ import annotations

import sqlite3

import pandas as pd
from strategy import risk

THEME_KEYWORDS = ("科技", "人工智能", "AI", "机器人", "芯片", "半导体", "新能源", "军工", "医药", "软件", "通信", "传媒")


def classify_style(name: str) -> str:
    return "题材" if any(k in str(name) for k in THEME_KEYWORDS) else "价值"


def save_positions(db_path: str, positions: pd.DataFrame) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS positions ("
            "code TEXT PRIMARY KEY, name TEXT, shares REAL, cost REAL, style TEXT)"
        )
        for _, r in positions.iterrows():
            conn.execute(
                "INSERT OR REPLACE INTO positions (code,name,shares,cost,style) VALUES (?,?,?,?,?)",
                (r["code"], r["name"], r["shares"], r["cost"], r.get("style", classify_style(r["name"]))),
            )
        conn.commit()
    finally:
        conn.close()


def load_positions(db_path: str) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS positions ("
            "code TEXT PRIMARY KEY, name TEXT, shares REAL, cost REAL, style TEXT)"
        )
        conn.commit()
        df = pd.read_sql_query("SELECT * FROM positions", conn)
    finally:
        conn.close()
    if df.empty:
        df = pd.DataFrame(columns=["code", "name", "shares", "cost", "style"])
    return df


def diagnose_positions(db_path, quotes: pd.DataFrame, rec: pd.DataFrame, settings) -> pd.DataFrame:
    pos = load_positions(db_path)
    if pos.empty:
        return pos
    q = quotes[["code", "price"]].copy()
    df = pos.merge(q, on="code", how="left")
    df["pnl"] = (df["price"] - df["cost"]) * df["shares"]
    df["pnl_pct"] = df["price"] / df["cost"] - 1
    equity = max(float(settings.account.cash) + (df["shares"] * df["price"]).sum(), 1e-6)
    policy = risk.current_policy(settings)
    df["current_weight"] = df["shares"] * df["price"] / equity
    if df['price'].isna().any():
        # Partial valuations cannot establish total equity or portfolio weights.
        df['current_weight'] = float('nan')
    rec_cols = [c for c in ["code", "score_blend", "rating", "suggested_weight"] if c in rec.columns]
    if rec_cols:
        df = df.merge(rec[rec_cols], on="code", how="left")
    if "rating" in df.columns:
        df["rating"] = df["rating"].fillna("中性")
    else:
        df["rating"] = "中性"
    actions = []
    for _, r in df.iterrows():
        raw_score = r.get("score_blend", float("nan"))
        if not risk.positive(r.get("price")) or not risk.positive(r.get("cost")):
            actions.append(("数据不足", "缺少有效现价或成本，无法核实盈亏与仓位"))
            continue
        if pd.isna(raw_score):
            action = "数据不足"
            reason = "当前交易日没有可核实的推荐评分"
            actions.append((action, reason))
            continue
        score = float(raw_score)
        if not risk.buying_allowed(policy) and score >= 60:
            action = "持有"
            reason = "宏观防守档禁止买入与加仓"
        elif score >= 80:
            target = min(settings.risk.max_single_position,
                         risk.positive(r.get("suggested_weight")) if "suggested_weight" in df else settings.risk.max_single_position)
            if r["current_weight"] < target:
                action = "加仓"
                reason = "信号强烈看多且尚未达到目标仓位"
            else:
                action = "持有"
                reason = "已达到或超过目标仓位，不再追加"
        elif score >= 60:
            action = "持有"
            reason = "信号看多，继续持有观察"
        elif score < 20:
            action = "清仓"
            reason = "信号强烈看空"
        elif score < 40:
            action = "减仓" if r["current_weight"] > 0.1 else "清仓"
            reason = "信号偏空，降低敞口"
        else:
            action = "持有"
            reason = "信号中性，等待方向"
        actions.append((action, reason))
    df["action"], df["reason"] = zip(*actions) if actions else ([], [])
    return df[["code", "name", "shares", "cost", "price", "pnl", "pnl_pct", "current_weight", "rating", "action", "reason", "style"]]


def portfolio_warnings(db_path, quotes: pd.DataFrame, rec: pd.DataFrame, settings) -> list[str]:
    df = diagnose_positions(db_path, quotes, rec, settings)
    if df.empty:
        return ["暂无持仓"]
    warns = []
    for _, r in df.iterrows():
        if r["current_weight"] > settings.risk.max_single_position:
            warns.append(f"{r['name']} 仓位 {r['current_weight']:.0%} 超过单票上限 {settings.risk.max_single_position:.0%}")
    theme_w = df.loc[df["style"] == "题材", "current_weight"].sum()
    if theme_w > 0.5:
        warns.append(f"题材股合计仓位 {theme_w:.0%} 超过 50%，注意集中度风险")
    return warns
