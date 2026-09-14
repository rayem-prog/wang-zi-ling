"""盘中即时决策与异动雷达 API。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter

from config.settings import load_settings
from data import db, fetcher
from strategy import holdings

router = APIRouter(prefix="/api/intraday", tags=["intraday"])


@router.get("/decisions")
def get_intraday_decisions() -> dict[str, Any]:
    settings = load_settings()
    stop_loss_pct = float(settings.risk.stop_loss)
    take_profit_pct = float(settings.risk.take_profit)

    # 1. 持仓动态决策计算
    holdings_df = holdings.load_positions(settings.data.db_path)
    holdings_decisions = []
    urgent_count = 0
    take_profit_count = 0

    if not holdings_df.empty:
        holdings_df["code"] = holdings_df["code"].astype(str).str.zfill(6)
        codes = holdings_df["code"].tolist()

        # 获取实时行情
        try:
            spot = fetcher.fetch_spot_batch(codes)
        except Exception:
            spot = pd.DataFrame()

        if spot.empty:
            spot = db.load_spot(settings.data.db_path)

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
            cur_price = float(price_map.get(code, cost))
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
    orders_path = Path("artifacts/latest_orders.csv")
    if orders_path.exists():
        try:
            ord_df = pd.read_csv(orders_path, dtype={"code": str})
            if not ord_df.empty:
                ord_df["code"] = ord_df["code"].astype(str).str.zfill(6)
                ord_codes = ord_df["code"].tolist()

                try:
                    ord_spot = fetcher.fetch_spot_batch(ord_codes)
                except Exception:
                    ord_spot = pd.DataFrame()

                if not ord_spot.empty:
                    ord_spot["code"] = ord_spot["code"].astype(str).str.zfill(6)
                    sp_map = dict(zip(ord_spot["code"], ord_spot["price"]))
                    pct_sp_map = dict(zip(ord_spot["code"], ord_spot.get("pct_change", [0.0] * len(ord_spot))))

                    for _, o in ord_df.iterrows():
                        c = o["code"]
                        name = o.get("name", c)
                        p = float(sp_map.get(c, 0.0))
                        pct = float(pct_sp_map.get(c, 0.0))
                        p_low = float(o.get("price_low", 0.0))
                        p_high = float(o.get("price_high", 999999.0))

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
        except Exception:
            pass

    return {
        "holdings_decisions": holdings_decisions,
        "orders_triggers": orders_triggers,
        "summary": {
            "urgent_count": urgent_count,
            "take_profit_count": take_profit_count,
            "in_range_count": in_range_count,
            "total_holdings": len(holdings_decisions),
        },
    }


@router.get("/radar")
def get_intraday_radar() -> list[dict[str, Any]]:
    """盘中实时异动机会扫描（多头动量与突破标的）。"""
    settings = load_settings()
    radar_candidates = []

    # 优先从自选 + 历史推荐池中抓取高频异动
    watch_pool = set(settings.universe.extra_codes)
    rec_path = Path("artifacts/recommendations.csv")
    if rec_path.exists():
        try:
            rdf = pd.read_csv(rec_path, dtype={"code": str})
            watch_pool.update(rdf["code"].astype(str).str.zfill(6).tolist())
        except Exception:
            pass

    if not watch_pool:
        watch_pool = {"000001", "600519", "000858", "601318", "300750", "002594", "601899", "600036"}

    try:
        spot_df = fetcher.fetch_spot_batch(sorted(watch_pool))
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
    except Exception:
        pass

    return radar_candidates[:15]
