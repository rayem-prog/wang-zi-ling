"""盘中即时决策与异动雷达 API。"""

from __future__ import annotations

from pathlib import Path
import datetime as dt
import sqlite3
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException

from services.decision_results import operation_frame
from config.settings import load_settings
from strategy import risk
from data import db, fetcher
from data.health import _expected_session, assess_data_health, recommendation_state
from data.timeliness import quote_state, exchange_now
from api.health_cache import health_cache
from strategy import holdings

router = APIRouter(prefix="/api/intraday", tags=["intraday"])


def _fresh_quotes(quotes: pd.DataFrame) -> pd.DataFrame:
    if quotes.empty:
        return pd.DataFrame(columns=["code", "price", "pct_change"])
    return quotes.loc[[not quote_state(row)["stale"] for row in quotes.to_dict("records")]].copy()


@router.get("/decisions")
def get_intraday_decisions() -> dict[str, Any]:
    settings = load_settings()
    stop_loss_pct = float(settings.risk.stop_loss)
    take_profit_pct = float(settings.risk.take_profit)
    errors = []

    # 1. 持仓动态决策计算
    holdings_df = holdings.load_positions(settings.data.db_path)
    holdings_decisions = []
    urgent_count = 0
    take_profit_count = 0
    quote_states = {}

    if not holdings_df.empty:
        holdings_df["code"] = holdings_df["code"].astype(str).str.zfill(6)
        codes = holdings_df["code"].tolist()

        # 获取实时行情
        try:
            spot = fetcher.fetch_spot_batch(codes)
        except Exception as exc:
            errors.append(f"持仓行情采集失败: {exc}")
            spot = pd.DataFrame()

        if spot.empty:
            spot = db.load_spot(settings.data.db_path)
        if not spot.empty:
            quote_states = {str(r["code"]).zfill(6): quote_state(r) for r in spot.to_dict("records")}
        spot = _fresh_quotes(spot)

        price_map = {}
        pct_map = {}
        if not spot.empty:
            spot["code"] = spot["code"].astype(str).str.zfill(6)
            price_map = dict(zip(spot["code"], spot["price"]))
            pct_map = dict(zip(spot["code"], spot.get("pct_change", [0.0] * len(spot))))

        for _, row in holdings_df.iterrows():
            code = row["code"]
            name = row["name"]
            shares = float(row["shares"])
            cost = float(row["cost"])
            state = quote_states.get(code, dict(stale=True, trade_date=None, quote_time=None, price_source=None))
            metadata = {"price_stale": state["stale"], "trade_date": state["trade_date"],
                        "quote_time": state["quote_time"], "price_source": state["price_source"]}
            if code not in price_map:
                holdings_decisions.append({
                    "code": code, "name": name, "shares": shares, "cost": cost, **metadata,
                    "price": None, "today_pct": None, "pnl_pct": None,
                    "status": "DATA_UNAVAILABLE", "level": "ERROR",
                    "action_badge": "行情不可用",
                    "advice": "缺少对应交易日的真实快照，无法判断止损或止盈",
                })
                continue
            cur_price = float(price_map[code])
            today_pct = float(pct_map.get(code, 0.0))

            pnl_pct = (cur_price - cost) / cost if cost > 0 else 0.0
            dist_to_stop = pnl_pct - (-stop_loss_pct)
            dist_to_take = take_profit_pct - pnl_pct

            # 决策分类与建议
            if pnl_pct <= -stop_loss_pct:
                status = "STOP_LOSS"
                level = "P0"
                action_badge = "🔴 立即硬止损"
                advice = f"浮亏达 {pnl_pct:.2%}，已跌穿 -{stop_loss_pct:.1%} 硬止损线，纪律第一，建议立即市价离场！"
                urgent_count += 1
            elif pnl_pct >= take_profit_pct:
                status = "TAKE_PROFIT"
                level = "P0"
                action_badge = "🟢 达标目标止盈"
                advice = f"浮盈达 {pnl_pct:.2%}，已达 +{take_profit_pct:.1%} 目标收益位，建议分批止盈锁定利润！"
                take_profit_count += 1
            elif -stop_loss_pct < pnl_pct <= -stop_loss_pct * 0.6:
                status = "WARNING_LOSS"
                level = "P1"
                action_badge = "🟠 临界预警"
                advice = f"浮亏达 {pnl_pct:.2%}，距硬止损线仅差 {abs(dist_to_stop):.2%}，请严密监控盘面支撑！"
            elif pnl_pct >= take_profit_pct * 0.7:
                status = "APPROACH_PROFIT"
                level = "P1"
                action_badge = "🟡 临近止盈"
                advice = f"浮盈达 {pnl_pct:.2%}，稳步逼近止盈目标，可考虑上移保本止损点保护盈利。"
            else:
                status = "SAFE_HOLD"
                level = "P2"
                action_badge = "⚪ 安全持有"
                advice = "处于健康波动区间，无破位破均线风险，遵从策略持股。"

            holdings_decisions.append({
                "code": code,
                **metadata,
                "name": name,
                "shares": shares,
                "cost": round(cost, 2),
                "price": round(cur_price, 2),
                "pnl_pct": round(pnl_pct, 4),
                "today_pct": round(today_pct, 2),
                "dist_to_stop": round(dist_to_stop, 4),
                "dist_to_take": round(dist_to_take, 4),
                "status": status,
                "level": level,
                "action_badge": action_badge,
                "advice": advice,
            })

    # 2. 昨日指令单执行状态监控
    orders_triggers = []
    in_range_count = 0
    order_state = recommendation_state(settings, health_cache.get(settings))
    order_ready = order_state["ready"]
    if not order_ready:
        errors.append(f"指令单暂停: {order_state['reason']}")
    orders_path = Path(settings.paths.artifact_dir) / "latest_orders.csv"
    if order_ready and risk.buying_allowed(risk.current_policy(settings)):
        try:
            ord_df = operation_frame(settings, order_state, "orders")
            if not ord_df.empty:
                ord_df["code"] = ord_df["code"].astype(str).str.zfill(6)
                ord_codes = ord_df["code"].tolist()

                try:
                    ord_spot = fetcher.fetch_spot_batch(ord_codes)
                except Exception as exc:
                    errors.append(f"指令单行情采集失败: {exc}")
                    ord_spot = pd.DataFrame()
                ord_spot = _fresh_quotes(ord_spot)

                if not ord_spot.empty:
                    ord_spot["code"] = ord_spot["code"].astype(str).str.zfill(6)
                    sp_map = dict(zip(ord_spot["code"], ord_spot["price"]))
                    pct_sp_map = dict(zip(ord_spot["code"], ord_spot.get("pct_change", [0.0] * len(ord_spot))))

                    for _, o in ord_df.iterrows():
                        if str(o.get("action", "")) not in {"买入", "加仓"}:
                            continue
                        c = o["code"]
                        name = o.get("name", c)
                        p = float(sp_map.get(c, 0.0))
                        pct = float(pct_sp_map.get(c, 0.0))
                        p_low = float(o["limit_low"])
                        p_high = float(o["limit_high"])

                        # 涨停放弃
                        if pct >= 9.8:
                            orders_triggers.append({
                                "code": c,
                                "name": name,
                                "price": round(p, 2),
                                "status": "LIMIT_UP_ABORT",
                                "badge": "⚠️ 涨停放弃",
                                "message": f"开盘高开 {pct:+.2f}% 逼近涨停，坚决放弃追高买入！",
                            })
                        elif p_low <= p <= p_high and p > 0:
                            orders_triggers.append({
                                "code": c,
                                "name": name,
                                "price": round(p, 2),
                                "status": "IN_RANGE",
                                "badge": "🔔 到位建仓",
                                "message": f"现价 ¥{p:.2f} 进入计划区间 [¥{p_low:.2f} ~ ¥{p_high:.2f}]，符合买入条件！",
                            })
                            in_range_count += 1
        except Exception as exc:
            errors.append(f"指令单读取失败: {exc}")

    return {
        "holdings_decisions": holdings_decisions,
        "orders_triggers": orders_triggers,
        "errors": errors,
        "summary": {
            "urgent_count": urgent_count,
            "take_profit_count": take_profit_count,
            "in_range_count": in_range_count,
            "total_holdings": len(holdings_decisions),
            "stale_count": sum(row["price_stale"] for row in holdings_decisions),
            "timestamp": exchange_now().isoformat(timespec="seconds"),
        },
    }


@router.get("/radar")
def get_intraday_radar() -> list[dict[str, Any]]:
    """盘中实时异动机会扫描（多头动量与突破标的）。"""
    settings = load_settings()
    rec_state = recommendation_state(settings, health_cache.get(settings))
    if not rec_state["ready"]:
        return []
    radar_candidates = []

    # 优先从自选 + 历史推荐池中抓取高频异动
    watch_pool = set(settings.universe.extra_codes)
    with sqlite3.connect(settings.data.db_path) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='user_watchlist'").fetchone():
            watch_pool.update(row[0] for row in conn.execute("SELECT code FROM user_watchlist"))
    rdf = operation_frame(settings, rec_state)
    if not rdf.empty:
        watch_pool.update(rdf["code"].astype(str).str.zfill(6).tolist())

    if not watch_pool:
        return []

    try:
        spot_df = _fresh_quotes(fetcher.fetch_spot_batch(sorted(watch_pool)))
        if spot_df.empty:
            raise RuntimeError("异动雷达缺少对应交易日的真实行情")
        if not spot_df.empty:
            for _, r in spot_df.iterrows():
                code = str(r["code"]).zfill(6)
                name = str(r.get("name", code))
                price = float(r.get("price", 0.0))
                pct = float(r.get("pct_change", 0.0))
                volume = float(r.get("volume", 0.0))

                # 筛选条件：日内放量强势但未达涨停（涨幅 1.5% ~ 8.0%）
                tags = []
                if 2.0 <= pct <= 7.5:
                    tags.append("日内动量走强")
                if pct > 0:
                    tags.append("均价线上方")
                if volume > 50000:
                    tags.append("资金放量抢筹")

                if tags and price > 0:
                    score = pct * 0.6 + (1.5 if "资金放量抢筹" in tags else 0.5)
                    radar_candidates.append({
                        "code": code,
                        "name": name,
                        "price": round(price, 2),
                        "pct_change": round(pct, 2),
                        "volume": int(volume),
                        "tags": tags,
                        "score": round(score, 2),
                    })

            radar_candidates.sort(key=lambda x: x["score"], reverse=True)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"异动雷达不可用: {exc}") from exc

    return radar_candidates[:15]
