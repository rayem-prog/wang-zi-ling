"""M4 模拟盘账本与模拟交易引擎：真实资金账户、价格档位撮合、持仓随机化与压力测试。"""

from __future__ import annotations

import datetime as dt
import random
import sqlite3
from pathlib import Path
from typing import Any

DEFAULT_INITIAL_CASH = 1_000_000.0

SAMPLE_STOCK_BASKET = [
    # 黄金低价池 (≤¥20，适合中小散户与低门槛分批)
    ("000725", "京东方A", 4.25),
    ("601988", "中国银行", 5.10),
    ("600019", "宝钢股份", 6.85),
    ("000001", "平安银行", 10.50),
    ("601899", "紫金矿业", 16.50),
    ("600030", "中信证券", 19.80),
    # 稳健中价池 (¥20~¥50，绩优蓝筹白马)
    ("600900", "长江电力", 28.50),
    ("600036", "招商银行", 34.00),
    ("002475", "立讯精密", 38.50),
    ("601318", "中国平安", 48.00),
    # 成长中高价池 (¥50~¥100，高景气赛道成长股)
    ("600276", "恒瑞医药", 52.00),
    ("300124", "汇川技术", 62.00),
    # 百元核心资产 (>¥100，高权重标的)
    ("000858", "五粮液", 130.00),
    ("300750", "宁德时代", 195.00),
    ("002594", "比亚迪", 260.00),
    ("600519", "贵州茅台", 1450.00),
]


def _conn(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    with conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS paper_account ("
            "id INTEGER PRIMARY KEY, initial_cash REAL, cash REAL, updated_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS paper_positions ("
            "code TEXT PRIMARY KEY, name TEXT, shares REAL, avg_cost REAL, current_price REAL, updated_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS paper_orders ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, name TEXT, side TEXT, "
            "order_type TEXT, shares REAL, price REAL, amount REAL, status TEXT, timestamp TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS paper_realized ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, name TEXT, pnl REAL, timestamp TEXT)"
        )

        # 兼容旧表结构升级
        cur = conn.execute("PRAGMA table_info(paper_positions)")
        existing_pos_cols = {row["name"] for row in cur.fetchall()}
        for col, col_type in [("name", "TEXT"), ("current_price", "REAL"), ("updated_at", "TEXT")]:
            if col not in existing_pos_cols:
                conn.execute(f"ALTER TABLE paper_positions ADD COLUMN {col} {col_type}")

        cur = conn.execute("PRAGMA table_info(paper_orders)")
        existing_ord_cols = {row["name"] for row in cur.fetchall()}
        for col, col_type in [("name", "TEXT"), ("order_type", "TEXT"), ("amount", "REAL"), ("status", "TEXT"), ("timestamp", "TEXT")]:
            if col not in existing_ord_cols:
                conn.execute(f"ALTER TABLE paper_orders ADD COLUMN {col} {col_type}")

        cur = conn.execute("PRAGMA table_info(paper_realized)")
        existing_real_cols = {row["name"] for row in cur.fetchall()}
        for col, col_type in [("name", "TEXT"), ("timestamp", "TEXT")]:
            if col not in existing_real_cols:
                conn.execute(f"ALTER TABLE paper_realized ADD COLUMN {col} {col_type}")

        # 初始化账户
        row = conn.execute("SELECT id FROM paper_account WHERE id = 1").fetchone()
        if not row:
            conn.execute(
                "INSERT INTO paper_account (id, initial_cash, cash, updated_at) VALUES (1, ?, ?, ?)",
                (DEFAULT_INITIAL_CASH, DEFAULT_INITIAL_CASH, dt.datetime.now().isoformat()),
            )
    return conn


def get_paper_account(path: str, current_prices: dict[str, float] | None = None) -> dict[str, Any]:
    current_prices = current_prices or {}
    conn = _conn(path)
    try:
        acc_row = conn.execute("SELECT initial_cash, cash FROM paper_account WHERE id = 1").fetchone()
        initial_cash = float(acc_row["initial_cash"]) if acc_row else DEFAULT_INITIAL_CASH
        cash = float(acc_row["cash"]) if acc_row else DEFAULT_INITIAL_CASH

        pos_rows = conn.execute("SELECT code, name, shares, avg_cost, current_price FROM paper_positions").fetchall()
        positions = []
        market_value = 0.0
        unrealized_pnl = 0.0

        for r in pos_rows:
            code = str(r["code"])
            name = str(r["name"] or code)
            shares = float(r["shares"])
            avg_cost = float(r["avg_cost"])
            cur_p = r["current_price"]
            price = float(current_prices.get(code, cur_p if cur_p is not None else avg_cost))
            val = shares * price
            market_value += val
            pnl = val - (shares * avg_cost)
            unrealized_pnl += pnl
            pnl_pct = pnl / (shares * avg_cost) if avg_cost > 0 else 0.0

            positions.append({
                "code": code,
                "name": name,
                "shares": shares,
                "avg_cost": round(avg_cost, 2),
                "price": round(price, 2),
                "market_value": round(val, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 4),
            })

        # 已实现盈亏
        realized_row = conn.execute("SELECT SUM(pnl) AS sum_pnl FROM paper_realized").fetchone()
        realized_pnl = float(realized_row["sum_pnl"]) if realized_row and realized_row["sum_pnl"] is not None else 0.0

        total_equity = cash + market_value
        total_pnl = total_equity - initial_cash
        return_pct = total_pnl / initial_cash if initial_cash > 0 else 0.0

        orders_rows = conn.execute(
            "SELECT id, code, name, side, order_type, shares, price, amount, status, timestamp "
            "FROM paper_orders ORDER BY id DESC LIMIT 50"
        ).fetchall()
        orders = [dict(o) for o in orders_rows]

        return {
            "initial_cash": round(initial_cash, 2),
            "cash": round(cash, 2),
            "market_value": round(market_value, 2),
            "total_equity": round(total_equity, 2),
            "total_pnl": round(total_pnl, 2),
            "realized_pnl": round(realized_pnl, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "return_pct": round(return_pct, 4),
            "positions": positions,
            "orders": orders,
        }
    finally:
        conn.close()


def execute_paper_order(
    path: str,
    code: str,
    name: str,
    side: str,
    shares: float,
    price: float,
    order_type: str = "市价",
) -> dict[str, Any]:
    if shares <= 0 or price <= 0:
        raise ValueError("股数和价格必须大于 0")

    conn = _conn(path)
    now_str = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    side = side.upper()
    amount = shares * price

    try:
        acc_row = conn.execute("SELECT cash FROM paper_account WHERE id = 1").fetchone()
        cash = float(acc_row["cash"]) if acc_row else DEFAULT_INITIAL_CASH

        if side in ("BUY", "买入"):
            if amount > cash:
                raise ValueError(f"可用资金不足！所需 ¥{amount:,.2f}，当前可用 ¥{cash:,.2f}")

            new_cash = cash - amount
            conn.execute("UPDATE paper_account SET cash = ?, updated_at = ? WHERE id = 1", (new_cash, now_str))

            pos_row = conn.execute("SELECT shares, avg_cost FROM paper_positions WHERE code = ?", (code,)).fetchone()
            if pos_row:
                old_sh = float(pos_row["shares"])
                old_cost = float(pos_row["avg_cost"])
                new_sh = old_sh + shares
                new_avg = (old_sh * old_cost + amount) / new_sh
                conn.execute(
                    "UPDATE paper_positions SET shares = ?, avg_cost = ?, current_price = ?, updated_at = ? WHERE code = ?",
                    (new_sh, new_avg, price, now_str, code),
                )
            else:
                conn.execute(
                    "INSERT INTO paper_positions (code, name, shares, avg_cost, current_price, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (code, name, shares, price, price, now_str),
                )

            conn.execute(
                "INSERT INTO paper_orders (code, name, side, order_type, shares, price, amount, status, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (code, name, "买入", order_type, shares, price, amount, "已成交", now_str),
            )

        elif side in ("SELL", "卖出"):
            pos_row = conn.execute("SELECT shares, avg_cost FROM paper_positions WHERE code = ?", (code,)).fetchone()
            if not pos_row or float(pos_row["shares"]) < shares:
                raise ValueError(f"持仓不足！可用持仓 {pos_row['shares'] if pos_row else 0} 股，拟卖出 {shares} 股")

            old_sh = float(pos_row["shares"])
            old_cost = float(pos_row["avg_cost"])
            realized_pnl = shares * (price - old_cost)

            new_cash = cash + amount
            conn.execute("UPDATE paper_account SET cash = ?, updated_at = ? WHERE id = 1", (new_cash, now_str))

            conn.execute(
                "INSERT INTO paper_realized (code, name, pnl, timestamp) VALUES (?, ?, ?, ?)",
                (code, name, realized_pnl, now_str),
            )

            left_sh = old_sh - shares
            if left_sh <= 0:
                conn.execute("DELETE FROM paper_positions WHERE code = ?", (code,))
            else:
                conn.execute(
                    "UPDATE paper_positions SET shares = ?, current_price = ?, updated_at = ? WHERE code = ?",
                    (left_sh, price, now_str, code),
                )

            conn.execute(
                "INSERT INTO paper_orders (code, name, side, order_type, shares, price, amount, status, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (code, name, "卖出", order_type, shares, price, amount, "已成交", now_str),
            )
        else:
            raise ValueError(f"未知交易方向: {side}")

        conn.commit()
        return {"status": "success", "code": code, "side": side, "shares": shares, "price": price, "amount": amount}
    finally:
        conn.close()


def reset_paper_account(path: str, initial_cash: float = DEFAULT_INITIAL_CASH) -> dict[str, Any]:
    conn = _conn(path)
    now_str = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with conn:
            conn.execute("DELETE FROM paper_positions")
            conn.execute("DELETE FROM paper_orders")
            conn.execute("DELETE FROM paper_realized")
            conn.execute(
                "UPDATE paper_account SET initial_cash = ?, cash = ?, updated_at = ? WHERE id = 1",
                (initial_cash, initial_cash, now_str),
            )
        return {"status": "reset_success", "cash": initial_cash}
    finally:
        conn.close()


def generate_random_portfolio(path: str, count: int = 4) -> dict[str, Any]:
    """一键随机生成真实模拟持仓组合。"""
    reset_paper_account(path)
    chosen = random.sample(SAMPLE_STOCK_BASKET, min(count, len(SAMPLE_STOCK_BASKET)))
    allocated_cash_per_stock = (DEFAULT_INITIAL_CASH * 0.7) / len(chosen)

    for code, name, base_price in chosen:
        # 随机浮动价格 +/- 5%
        price = round(base_price * random.uniform(0.95, 1.05), 2)
        shares = max(100, int(allocated_cash_per_stock / price / 100) * 100)
        execute_paper_order(path, code, name, "BUY", shares, price, order_type="随机初始化")

    return get_paper_account(path)


def apply_random_price_shock(path: str) -> dict[str, Any]:
    """对当前模拟持仓随机施加日内极端波动（用于压力测试与止损止盈预警演示）。"""
    conn = _conn(path)
    now_str = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    updated = []

    try:
        rows = conn.execute("SELECT code, name, avg_cost FROM paper_positions").fetchall()
        for r in rows:
            code = str(r["code"])
            avg_cost = float(r["avg_cost"])
            # 随机收益率区间 [-8%, +18%] 触发止损或止盈
            shock_pct = random.choice([-0.065, -0.045, -0.015, +0.035, +0.075, +0.165])
            shock_price = round(avg_cost * (1.0 + shock_pct), 2)

            conn.execute(
                "UPDATE paper_positions SET current_price = ?, updated_at = ? WHERE code = ?",
                (shock_price, now_str, code),
            )
            updated.append({"code": code, "shock_pct": shock_pct, "price": shock_price})

        conn.commit()
        return {"status": "shock_applied", "updated": updated}
    finally:
        conn.close()


# 兼容既有接口
def open_paper_order(path: str, code: str, shares: float, price: float) -> None:
    execute_paper_order(path, code, code, "BUY", shares, price, "市价")


def close_paper_order(path: str, code: str, shares: float, price: float) -> None:
    execute_paper_order(path, code, code, "SELL", shares, price, "市价")


def paper_summary(path: str, prices: dict[str, float]) -> dict:
    acc = get_paper_account(path, prices)
    return {
        "unrealized": acc["unrealized_pnl"],
        "realized": acc["realized_pnl"],
        "shares": sum(p["shares"] for p in acc["positions"]),
        "cash": acc["cash"],
        "total_equity": acc["total_equity"],
        "return_pct": acc["return_pct"],
    }


def generate_ai_trading_plan(path: str, risk_pref: str = "balanced") -> dict[str, Any]:
    """根据模拟账户当前可用现金、总资产及选股多因子综合评分，生成 AI 自主建仓模拟推演（价格区间与推荐股数）。"""
    acc = get_paper_account(path)
    cash = acc["cash"]
    equity = acc["total_equity"]
    held_codes = {p["code"] for p in acc["positions"]}

    # 风险偏好档位配置
    risk_configs = {
        "conservative": {"label": "稳健防守型", "max_single_ratio": 0.15, "max_stocks": 3},
        "balanced": {"label": "平衡稳进型", "max_single_ratio": 0.25, "max_stocks": 4},
        "aggressive": {"label": "进取突破型", "max_single_ratio": 0.35, "max_stocks": 4},
    }
    cfg = risk_configs.get(risk_pref, risk_configs["balanced"])
    max_single_ratio = cfg["max_single_ratio"]

    # 获取候选标的
    candidates = []
    rec_file = Path("artifacts/recommendations.csv")
    if rec_file.exists():
        try:
            import pandas as pd
            df = pd.read_csv(rec_file, dtype={"code": str})
            if not df.empty and "code" in df.columns:
                for _, r in df.head(8).iterrows():
                    c_code = str(r["code"]).zfill(6)
                    c_name = str(r.get("name", c_code))
                    c_price = float(r.get("price", 0.0))
                    c_score = float(r.get("score_blend", 0.8))
                    candidates.append((c_code, c_name, c_price, c_score))
        except Exception:
            candidates = []

    if not candidates:
        for code, name, base_price in SAMPLE_STOCK_BASKET:
            candidates.append((code, name, base_price, 0.82))

    # 优先推荐单价亲民、流动性高、适合分批建仓的优质标的，避免百元高价股过早吞噬仓位
    candidates.sort(key=lambda x: (1 if x[2] > 100 else 0, -x[3], x[2]))

    recommendations = []
    # 过滤与分配
    for code, name, price, score in candidates:
        if len(recommendations) >= cfg["max_stocks"]:
            break
        if price <= 0:
            continue
        # 价格区间构建
        range_low = round(price * 0.988, 2)
        range_high = round(price * 1.012, 2)
        optimal_entry = round(price * 0.997, 2)

        # 推荐股数计算：根据单票预算与可用资金双重约束，向下取整 100 股
        max_budget = equity * max_single_ratio
        available_budget = min(cash * 0.5, max_budget)
        if available_budget < optimal_entry * 100:
            if cash >= optimal_entry * 100:
                shares = 100
            else:
                shares = 0
        else:
            shares = int(available_budget / optimal_entry / 100) * 100

        if shares <= 0:
            continue

        est_amount = round(shares * optimal_entry, 2)
        pos_pct = round(est_amount / equity * 100, 1) if equity > 0 else 0.0

        half_shares = max(100, (shares // 200) * 100)
        rest_shares = max(100, shares - half_shares) if shares > half_shares else half_shares
        ladder = [
            {
                "tier": "区间下沿 (买二档低吸)",
                "price": range_low,
                "shares": half_shares,
                "amount": round(half_shares * range_low, 2),
            },
            {
                "tier": "区间中枢 (买一档稳健)",
                "price": optimal_entry,
                "shares": rest_shares,
                "amount": round(rest_shares * optimal_entry, 2),
            },
        ]

        target_price = round(price * 1.15, 2)
        stop_loss = round(price * 0.95, 2)

        is_held = code in held_codes

        ai_rationale = (
            f"多模型综合评分 {score:.2f}。"
            f"AI 建议在价格区间 [¥{range_low:.2f} ~ ¥{range_high:.2f}] 内分批挂单吸筹，"
            f"推荐建仓 {shares} 股（预估占用资金 ¥{est_amount:,.2f}，占总资产 {pos_pct}%）。"
            f"目标止盈价 ¥{target_price:.2f} (+15.0%)，硬止损保护线 ¥{stop_loss:.2f} (-5.0%)。"
        )

        recommendations.append({
            "code": code,
            "name": name,
            "current_price": price,
            "price_range_low": range_low,
            "price_range_high": range_high,
            "optimal_entry": optimal_entry,
            "recommended_shares": shares,
            "estimated_amount": est_amount,
            "position_pct": pos_pct,
            "target_price": target_price,
            "stop_loss_price": stop_loss,
            "score": round(score, 3),
            "is_held": is_held,
            "ladder": ladder,
            "ai_rationale": ai_rationale,
        })

    return {
        "status": "success",
        "risk_pref": risk_pref,
        "risk_label": cfg["label"],
        "max_single_ratio": max_single_ratio,
        "available_cash": round(cash, 2),
        "total_equity": round(equity, 2),
        "recommendations": recommendations,
    }


def execute_ai_trading_plan(
    path: str,
    recommendations: list[dict] | None = None,
    risk_pref: str = "balanced",
) -> dict[str, Any]:
    """AI 一键自主撮合执行模拟建仓。"""
    if not recommendations:
        plan = generate_ai_trading_plan(path, risk_pref)
        recommendations = plan.get("recommendations", [])

    executed = []
    for item in recommendations:
        code = item["code"]
        name = item.get("name", code)
        shares = item["recommended_shares"]
        price = item.get("optimal_entry", item.get("current_price", 10.0))
        try:
            res = execute_paper_order(path, code, name, "BUY", shares, price, order_type="AI自主模拟建仓")
            executed.append(res)
        except Exception as e:
            executed.append({"code": code, "status": "skipped", "reason": str(e)})

    acc = get_paper_account(path)
    return {
        "status": "ai_execution_completed",
        "executed_count": len([e for e in executed if e.get("status") == "success"]),
        "details": executed,
        "account": acc,
    }
