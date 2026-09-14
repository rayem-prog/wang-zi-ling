"""动态权重（见设计文档第 6 节）与合并分。"""

from __future__ import annotations

import sqlite3

import pandas as pd


def adjusted_weights(er_a: float, er_b: float, w_old: float, w_min: float = 0.2, w_max: float = 0.8) -> tuple[float, float]:
    ea = max(0.0, er_a)
    eb = max(0.0, er_b)
    wprime = 0.5 if ea + eb == 0 else ea / (ea + eb)
    wa = 0.5 * w_old + 0.5 * wprime
    wa = min(w_max, max(w_min, wa))
    return round(wa, 4), round(1 - wa, 4)


def merge_scores(df: pd.DataFrame, w_a: float) -> pd.DataFrame:
    out = df.copy()
    out["score_blend"] = w_a * out["score_a"] + (1 - w_a) * out["score_b"]
    return out


def init_weight_table(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS weight_history ("
            "ts TEXT DEFAULT (datetime('now')), w_a REAL, er_a REAL, er_b REAL)"
        )
        conn.commit()
    finally:
        conn.close()


def record_weights(db_path: str, w_a: float, er_a: float, er_b: float) -> None:
    init_weight_table(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO weight_history (w_a, er_a, er_b) VALUES (?,?,?)",
            (w_a, er_a, er_b),
        )
        conn.commit()
    finally:
        conn.close()


def load_weight_history(db_path: str) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    try:
        return pd.read_sql_query("SELECT * FROM weight_history ORDER BY ts", conn)
    finally:
        conn.close()


def current_weights(engine_settings, db_path: str) -> tuple[float, float]:
    if engine_settings.manual_override and engine_settings.manual_w_a is not None:
        wa = min(engine_settings.w_max, max(engine_settings.w_min, engine_settings.manual_w_a))
        return wa, 1 - wa
    try:
        hist = load_weight_history(db_path)
        if hist.empty:
            return engine_settings.w_a, engine_settings.w_b
        return float(hist.iloc[-1]["w_a"]), 1 - float(hist.iloc[-1]["w_a"])
    except Exception:
        return engine_settings.w_a, engine_settings.w_b


def evaluate_and_update_weights(
    today_df: pd.DataFrame,
    quotes_df: pd.DataFrame | None,
    bench_return: float,
    current_wa: float,
    db_path: str,
    w_min: float = 0.2,
    w_max: float = 0.8,
) -> dict:
    """
    根据当天全市场股票的实际涨跌幅，评估双引擎预测的有效性并自适应调整权重：
    1. 提取 score_a 前 20%（或 TOP 10）标的的实际平均收益率与胜率；
    2. 提取 score_b 前 20%（或 TOP 10）标的的实际平均收益率与胜率；
    3. 计算相对于基准 bench_return 的超额收益 er_a, er_b；
    4. 自适应更新权重，写入 SQLite 数据库 weight_history；
    5. 返回详细审计诊断字典。
    """
    df = today_df.copy()
    if df.empty or "score_a" not in df.columns or "score_b" not in df.columns:
        return {
            "old_w_a": current_wa,
            "old_w_b": round(1 - current_wa, 4),
            "new_w_a": current_wa,
            "new_w_b": round(1 - current_wa, 4),
            "weight_delta": 0.0,
            "r_a": 0.0,
            "r_b": 0.0,
            "er_a": 0.0,
            "er_b": 0.0,
            "win_rate_a": 50.0,
            "win_rate_b": 50.0,
            "bench_return": round(bench_return * 100, 2),
            "summary": "缺少打分数据，维持现有权重。",
        }

    # 获取标的当日实际收益率
    real_returns = {}
    if quotes_df is not None and not quotes_df.empty and "code" in quotes_df.columns:
        for _, q in quotes_df.iterrows():
            c = str(q["code"]).zfill(6)
            p_chg = float(q.get("pct_change", 0.0))
            real_returns[c] = p_chg / 100.0

    returns_list = []
    for _, r in df.iterrows():
        c = str(r["code"]).zfill(6)
        if c in real_returns:
            ret = real_returns[c]
        elif "ret_1" in r and not pd.isna(r["ret_1"]):
            ret = float(r["ret_1"])
        else:
            ret = 0.0
        returns_list.append(ret)

    df["real_ret"] = returns_list

    # 评估双引擎前 20%（至少 3 只，至多 15 只）高分标的表现
    n_sample = min(15, max(3, int(len(df) * 0.2)))

    top_a = df.sort_values("score_a", ascending=False).head(n_sample)
    top_b = df.sort_values("score_b", ascending=False).head(n_sample)

    r_a = float(top_a["real_ret"].mean()) if not top_a.empty else 0.0
    r_b = float(top_b["real_ret"].mean()) if not top_b.empty else 0.0

    win_a = float((top_a["real_ret"] > 0).mean()) if not top_a.empty else 0.5
    win_b = float((top_b["real_ret"] > 0).mean()) if not top_b.empty else 0.5

    er_a = r_a - bench_return
    er_b = r_b - bench_return

    # 综合考量超额收益与胜率，构建自适应激励参数
    eff_a = max(0.0001, er_a + (win_a - 0.5) * 0.04 + 0.02)
    eff_b = max(0.0001, er_b + (win_b - 0.5) * 0.04 + 0.02)

    new_wa, new_wb = adjusted_weights(eff_a, eff_b, current_wa, w_min, w_max)
    weight_delta = round(new_wa - current_wa, 4)

    # 存盘
    try:
        record_weights(db_path, new_wa, er_a, er_b)
    except Exception as e:
        print(f"写入权重历史失败: {e}")

    summary_text = (
        f"今日引擎 A (LightGBM) 实际收益 {r_a*100:+.2f}%，超额 {er_a*100:+.2f}%，胜率 {win_a*100:.1f}%；"
        f"引擎 B (多因子规则) 实际收益 {r_b*100:+.2f}%，超额 {er_b*100:+.2f}%，胜率 {win_b*100:.1f}%。"
        f"双引擎权重由 {current_wa:.2f}:{1-current_wa:.2f} 动态调整至 {new_wa:.2f}:{new_wb:.2f} (变动 {weight_delta:+.2%})。"
    )

    return {
        "old_w_a": current_wa,
        "old_w_b": round(1 - current_wa, 4),
        "new_w_a": new_wa,
        "new_w_b": new_wb,
        "weight_delta": weight_delta,
        "r_a": round(r_a * 100, 2),
        "r_b": round(r_b * 100, 2),
        "er_a": round(er_a * 100, 2),
        "er_b": round(er_b * 100, 2),
        "win_rate_a": round(win_a * 100, 1),
        "win_rate_b": round(win_b * 100, 1),
        "bench_return": round(bench_return * 100, 2),
        "summary": summary_text,
    }

