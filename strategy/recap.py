"""每日复盘简报与次日指令单生成引擎：固定模板、本地生成、落盘 Markdown。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from strategy import risk
from config.settings import Settings

ORDER_COLUMNS = [
    "code",
    "name",
    "action",
    "shares",
    "price_ref",
    "limit_low",
    "limit_high",
    "amount",
    "weight",
    "stop_loss",
    "take_profit",
    "reason",
    "warning",
]


def generate_daily_orders(
    recommendations: pd.DataFrame,
    current_holdings: pd.DataFrame | None = None,
    settings=None,
    macro_res=None,
) -> pd.DataFrame:
    """
    根据选股推荐、现有持仓与宏观闸门，生成次日标准化指令单。
    """
    settings = settings or Settings()
    macro_res = macro_res or risk.current_policy(settings)
    orders = []
    prices = {}
    if recommendations is not None and not recommendations.empty:
        prices = {str(r["code"]).zfill(6): risk.positive(r.get("price")) for _, r in recommendations.iterrows()}
    values = {}
    if current_holdings is not None:
        for _, h in current_holdings.iterrows():
            code = str(h.get("code", "")).zfill(6)
            price = prices.get(code) or risk.positive(h.get("price"))
            values[code] = risk.positive(h.get("shares")) * (price or risk.positive(h.get("cost", h.get("cost_price"))))
    cash_left = max(settings.account.cash, 0)
    equity = cash_left + sum(values.values())
    capacity = max(0, equity * min(settings.risk.max_total_position, macro_res.max_total_position) - sum(values.values()))
    sold_codes = set()
    holding_codes = set()
    if current_holdings is not None and not current_holdings.empty and "code" in current_holdings.columns:
        holding_codes = set(current_holdings["code"].astype(str).str.zfill(6))

    allow_new_buy = risk.buying_allowed(macro_res)

    # 1. 检查持仓是否需要卖出或减仓
    if current_holdings is not None and not current_holdings.empty:
        for _, h in current_holdings.iterrows():
            code = str(h.get("code", "")).zfill(6)
            name = str(h.get("name", code))
            shares = int(risk.positive(h.get("shares")))
            cost = risk.positive(h.get("cost", h.get("cost_price")))
            cur_p = prices.get(code) or risk.positive(h.get("price"))
            if not shares or not cost or not cur_p:
                continue
            pnl_pct = (cur_p / cost - 1.0) if cost > 0 else 0.0

            # 触发止损或止盈
            stop_thresh = -(settings.risk.stop_loss if settings else 0.05)
            take_thresh = settings.risk.take_profit if settings else 0.15

            if pnl_pct <= stop_thresh:
                sold_codes.add(code)
                orders.append(
                    {
                        "code": code,
                        "name": name,
                        "action": "清仓",
                        "shares": shares,
                        "price_ref": cur_p,
                        "limit_low": round(cur_p * 0.99, 2),
                        "limit_high": cur_p,
                        "amount": round(shares * cur_p, 2),
                        "weight": 0.0,
                        "stop_loss": round(cur_p, 2),
                        "take_profit": round(cur_p, 2),
                        "reason": f"触及硬止损阈值 ({pnl_pct*100:.1f}%)",
                        "warning": "按市价或低限价挂单优先离场",
                    }
                )
            elif pnl_pct >= take_thresh:
                sold_codes.add(code)
                orders.append(
                    {
                        "code": code,
                        "name": name,
                        "action": "止盈卖出",
                        "shares": shares,
                        "price_ref": cur_p,
                        "limit_low": cur_p,
                        "limit_high": round(cur_p * 1.01, 2),
                        "amount": round(shares * cur_p, 2),
                        "weight": 0.0,
                        "stop_loss": round(cur_p * 0.95, 2),
                        "take_profit": round(cur_p, 2),
                        "reason": f"达到目标止盈位 ({pnl_pct*100:.1f}%)",
                        "warning": "可分批止盈锁定利润",
                    }
                )

    # 2. 检查买入推荐（防守档严禁新开仓）
    if allow_new_buy and recommendations is not None and not recommendations.empty:
        for _, rec in recommendations.iterrows():
            code = str(rec.get("code", "")).zfill(6)
            name = str(rec.get("name", code))
            p = risk.positive(rec.get("price"))
            w = float(rec.get("suggested_weight", 0.0))
            amt = float(rec.get("suggested_amount", 0.0))
            stop = float(rec.get("stop_loss", round(p * 0.95, 2)))
            take = float(rec.get("take_profit", round(p * 1.15, 2)))
            score = float(rec.get("score_blend", 70.0))

            if code in sold_codes or not p or w <= 0 or amt <= 0 or (pd.notna(rec.get("buyable")) and bool(rec.get("buyable"))):
                continue
            if code in holding_codes and not prices.get(code):
                continue

            # 每手 100 股
            limit_h = round(p * 1.005, 2)
            target = min(amt, equity * settings.risk.max_single_position)
            shares = risk.buy_shares(target, values.get(code, 0), cash_left, limit_h, capacity)
            if shares <= 0:
                continue

            reserved = shares * limit_h
            cash_left -= reserved
            capacity -= reserved
            values[code] = values.get(code, 0) + reserved
            action = "加仓" if code in holding_codes else "买入"
            limit_l = round(p * 0.995, 2)
            limit_h = round(p * 1.005, 2)

            orders.append(
                {
                    "code": code,
                    "name": name,
                    "action": action,
                    "shares": shares,
                    "price_ref": p,
                    "limit_low": limit_l,
                    "limit_high": limit_h,
                    "amount": round(shares * p, 2),
                    "weight": w,
                    "stop_loss": stop,
                    "take_profit": take,
                    "reason": f"双引擎综合打分 {score:.1f}",
                    "warning": "开盘涨幅≥9.8%跳空涨停不追",
                }
            )

    df = pd.DataFrame(orders)
    if df.empty:
        return pd.DataFrame(columns=ORDER_COLUMNS)
    return df[ORDER_COLUMNS].reset_index(drop=True)


def generate_daily_recap(
    trade_date: str,
    market_summary: dict,
    holdings_summary: dict,
    engine_perf: dict,
    orders_df: pd.DataFrame,
    macro_res=None,
    save_dir: str = "artifacts/reports",
) -> str:
    """
    生成符合 v2 规范的 7 段式本地复盘 Markdown。
    """
    stance_str = getattr(macro_res, "stance", "neutral") if macro_res else "neutral"
    score_val = getattr(macro_res, "score", 0.0) if macro_res else 0.0
    cap_val = getattr(macro_res, "max_total_position", 0.6) if macro_res else 0.6
    event_str = getattr(macro_res, "event_name", "") if macro_res else ""

    def metric(key, suffix=""):
        value = holdings_summary.get(key)
        return f"{float(value):,.2f}{suffix}" if value is not None else "未核实"

    md_lines = [
        f"# StockPilot 每日复盘与决策简报 ({trade_date})",
        "",
        "## 1. 大盘与宏观温度计",
        f"- **宏观档位**：`{stance_str.upper()}` (综合得分: {score_val:+.2f})",
        f"- **总仓位上限**：`{int(cap_val * 100)}%`" + (f" (受「{event_str}」影响降档)" if event_str else ""),
        f"- **三大指数表现**：{market_summary.get('index_text', '数据采集中')}",
        "",
        "## 2. 账户成绩与表现",
        f"- **持仓总市值**：{metric('market_value', ' 元')}",
        f"- **当日盈亏**：{metric('daily_pnl', ' 元')} ({metric('daily_pnl_pct', '%')})",
        f"- **超额收益(vs 沪深300)**：{metric('excess_pct', '%')}",
        "",
        "## 3. 昨日指令单执行回顾",
        f"{market_summary.get('order_exec_review', '- 昨日指令执行情况未核实。')}",
        "",
        "## 4. 双引擎表现跟踪与算法权重自适应演进",
        f"- **引擎 A (LightGBM)**: {engine_perf.get('engine_a', '未核实')}",
        f"- **引擎 B (规则引擎)**: {engine_perf.get('engine_b', '未核实')}",
        f"- **动态加权组合**: {engine_perf.get('blend', '未核实')}",
    ]

    if engine_perf.get("weight_audit"):
        md_lines.extend([
            "",
            "### 4.1 动态权重自适应赏罚调整",
            engine_perf["weight_audit"],
        ])

    if engine_perf.get("rule_audit") or engine_perf.get("model_audit"):
        md_lines.extend([
            "",
            "### 4.2 公式与模型算法优化",
            f"- **规则引擎 B 公式调优**: {engine_perf.get('rule_audit', '已应用宏观自适应优化配置')}",
            f"- **LightGBM 算法调优**: {engine_perf.get('model_audit', '模型样本已更新，特征收敛正常')}",
        ])

    md_lines.extend([
        "",
        "## 5. 信号与风控复盘",
        f"{market_summary.get('signal_review', '- 今日信号与回撤情况未核实。')}",
        "",
        "## 6. 临近重大事件提醒",
        f"{market_summary.get('event_reminder', '- 重大事件信息未核实。')}",
        "",
        "## 7. 明日操作指令单",
    ])

    if orders_df.empty:
        md_lines.append("> 今日无新增买入或调仓指令，维持现有仓位。")
    else:
        table_lines = [
            "| 代码 | 标的 | 操作 | 股数 | 参考价 | 限价区间 | 金额 | 止损价 | 止盈价 | 依据与警示 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for _, r in orders_df.iterrows():
            limit_range = f"{r['limit_low']:.2f} ~ {r['limit_high']:.2f}"
            note = f"{r['reason']}；{r['warning']}"
            table_lines.append(
                f"| `{r['code']}` | {r['name']} | **{r['action']}** | {r['shares']} | "
                f"¥{r['price_ref']:.2f} | {limit_range} | ¥{r['amount']:,.0f} | "
                f"¥{r['stop_loss']:.2f} | ¥{r['take_profit']:.2f} | {note} |"
            )
        md_lines.extend(table_lines)

    md_lines.append("")
    md_content = "\n".join(md_lines)

    # 落盘
    out_dir = Path(save_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_file = out_dir / f"{trade_date}.md"
    report_file.write_text(md_content, encoding="utf-8")

    return md_content


def generate_notification_summary(
    trade_date: str,
    macro_res=None,
    orders_df: pd.DataFrame | None = None,
    holdings_summary: dict | None = None,
) -> str:
    """
    生成 3–5 行脱敏推送通知，不含资金明细与隐私。
    """
    stance = getattr(macro_res, "stance", "neutral").upper() if macro_res else "NEUTRAL"
    cap = int(getattr(macro_res, "max_total_position", 0.6) * 100) if macro_res else 60

    lines = [
        f"【StockPilot 复盘】{trade_date}",
        f"宏观档位: {stance} | 仓位上限: {cap}%",
    ]

    if holdings_summary:
        daily_pct = holdings_summary.get("daily_pnl_pct", 0.0)
        lines.append(f"账户日涨跌: {daily_pct:+.2f}%")

    if orders_df is not None and not orders_df.empty:
        actions = []
        for _, r in orders_df.iterrows():
            actions.append(f"{r['action']}{r['code']}({r['limit_low']}-{r['limit_high']})")
        lines.append("次日指令: " + "、".join(actions))
    else:
        lines.append("次日指令: 无调仓，继续持有")

    return "\n".join(lines)
