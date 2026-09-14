"""盘中守护进程：09:25–15:05 分钟级监控持仓与自选，触发止损止盈(D)、执行择时(C1)、异动提醒(C2)，写入心跳与防休眠。"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import time

import pandas as pd

from config.settings import Settings, load_settings
from data import db, fetcher
from strategy.notify import CompositeNotifier, build_notifier


def is_trading_time(now: dt.datetime | None = None) -> bool:
    if now is None:
        now = dt.datetime.now()

    # 周一到周五
    if now.weekday() > 4:
        return False

    cur_time = now.time()
    morning_start = dt.time(9, 25)
    morning_end = dt.time(11, 30)
    afternoon_start = dt.time(13, 0)
    afternoon_end = dt.time(15, 5)

    return (morning_start <= cur_time <= morning_end) or (afternoon_start <= cur_time <= afternoon_end)


def check_intraday_triggers(
    spot_df: pd.DataFrame,
    orders_df: pd.DataFrame | None = None,
    holdings_df: pd.DataFrame | None = None,
    settings: Settings | None = None,
    notifier: CompositeNotifier | None = None,
) -> list[dict]:
    """
    检查一轮快照的触发项并发送分级通知。
    """
    if spot_df.empty or notifier is None:
        return []

    settings = settings or Settings()
    triggered = []
    spot_map = {}
    for _, r in spot_df.iterrows():
        spot_map[str(r["code"]).zfill(6)] = r

    # 1. 触发项 D: 监控持仓止损止盈 (P0 无冷却立即报警)
    if holdings_df is not None and not holdings_df.empty:
        for _, h in holdings_df.iterrows():
            code = str(h["code"]).zfill(6)
            name = str(h.get("name", code))
            cost = float(h.get("cost_price", 0.0))
            if cost <= 0 or code not in spot_map:
                continue

            cur_price = float(spot_map[code]["price"])
            pnl_pct = cur_price / cost - 1.0

            # 触发止损 (-5%)
            stop_thresh = -abs(settings.risk.stop_loss)
            if pnl_pct <= stop_thresh:
                title = f"【紧急止损 P0】{code} {name}"
                content = f"现价 ¥{cur_price:.2f} 触及止损线 (当前浮亏 {pnl_pct*100:.1f}%)，请立即离场止损！"
                notifier.send(title, content, level="P0", key=f"{code}:stop_loss")
                triggered.append({"code": code, "type": "stop_loss", "level": "P0", "price": cur_price})

            # 触发止盈 (+15%)
            take_thresh = abs(settings.risk.take_profit)
            if pnl_pct >= take_thresh:
                title = f"【达标止盈 P0】{code} {name}"
                content = f"现价 ¥{cur_price:.2f} 触及目标止盈线 (当前浮盈 {pnl_pct*100:.1f}%)，建议分批止盈！"
                notifier.send(title, content, level="P0", key=f"{code}:take_profit")
                triggered.append({"code": code, "type": "take_profit", "level": "P0", "price": cur_price})

    # 2. 触发项 C1: 监控昨日指令单买入标的
    if orders_df is not None and not orders_df.empty:
        for _, ord_item in orders_df.iterrows():
            code = str(ord_item["code"]).zfill(6)
            name = str(ord_item.get("name", code))
            action = str(ord_item.get("action", ""))

            if action not in ("买入", "加仓") or code not in spot_map:
                continue

            sp = spot_map[code]
            cur_price = float(sp["price"])
            pct_change = float(sp.get("pct_change", 0.0))
            limit_low = float(ord_item.get("limit_low", 0.0))
            limit_high = float(ord_item.get("limit_high", 999999.0))

            # 涨停不追
            if pct_change >= 9.8:
                title = f"【涨停放弃执行 P1】{code} {name}"
                content = f"开盘涨幅 {pct_change:+.2f}% 逼近涨停，按策略规则放弃追高！"
                notifier.send(title, content, level="P1", key=f"{code}:limit_up")
                triggered.append({"code": code, "type": "limit_up", "level": "P1", "price": cur_price})
                continue

            # 价格进入限价买入区间
            if limit_low <= cur_price <= limit_high:
                title = f"【指令执行择时 P1】{code} {name}"
                content = f"现价 ¥{cur_price:.2f} 进入计划买入区间 [¥{limit_low:.2f} ~ ¥{limit_high:.2f}]，可手动下单！"
                notifier.send(title, content, level="P1", key=f"{code}:in_range")
                triggered.append({"code": code, "type": "in_range", "level": "P1", "price": cur_price})

    return triggered


def run_daemon(settings: Settings | None = None, once: bool = False, poll_interval: int = 60):
    settings = settings or load_settings()
    db.init_db(settings.data.db_path)
    notifier = build_notifier(settings)

    # 启动 macOS 防休眠 (caffeinate)
    caffeinate_proc = None
    if not once:
        try:
            caffeinate_proc = subprocess.Popen(["caffeinate", "-d", "-i", "-s"])
        except Exception:
            pass

    pid = os.getpid()
    db.record_heartbeat(settings.data.db_path, status="started", message="守护进程已启动", pid=pid)

    try:
        while True:
            now = dt.datetime.now()
            if not is_trading_time(now) and not once:
                db.record_heartbeat(settings.data.db_path, status="standby", message="非交易时段，待机轮询中", pid=pid)
                time.sleep(poll_interval)
                continue

            try:
                # 获取监控列表：持仓 + 自选
                holdings_df = pd.DataFrame()
                try:
                    from strategy.paper import load_positions
                    holdings_df = load_positions(settings.paths.artifact_dir)
                except Exception:
                    pass

                # 获取最新指令单
                orders_df = pd.DataFrame()
                try:
                    orders_file = Path(settings.paths.artifact_dir) / "latest_orders.csv"
                    if orders_file.exists():
                        orders_df = pd.read_csv(orders_file)
                except Exception:
                    pass

                watch_codes = set()
                if not holdings_df.empty and "code" in holdings_df.columns:
                    watch_codes.update(holdings_df["code"].astype(str).str.zfill(6))
                if not orders_df.empty and "code" in orders_df.columns:
                    watch_codes.update(orders_df["code"].astype(str).str.zfill(6))
                for c in settings.universe.extra_codes:
                    watch_codes.add(str(c).zfill(6))

                if not watch_codes:
                    watch_codes = {"000001", "600519"}  # 默认兜底监控

                # 批量抓取盘中快照
                spot_df = fetcher.fetch_spot_batch(sorted(watch_codes))
                if not spot_df.empty:
                    db.save_spot(settings.data.db_path, spot_df)

                # 判定触发项并分级发送
                triggers = check_intraday_triggers(
                    spot_df=spot_df,
                    orders_df=orders_df,
                    holdings_df=holdings_df,
                    settings=settings,
                    notifier=notifier,
                )

                msg = f"监控 {len(spot_df)} 只标的，触发 {len(triggers)} 项提醒"
                db.record_heartbeat(settings.data.db_path, status="running", message=msg, pid=pid)

            except Exception as e:
                db.record_heartbeat(settings.data.db_path, status="error", message=f"轮询异常: {e}", pid=pid)

            if once:
                break
            time.sleep(poll_interval)

    finally:
        db.record_heartbeat(settings.data.db_path, status="stopped", message="守护进程已退出", pid=pid)
        if caffeinate_proc:
            try:
                caffeinate_proc.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="StockPilot 盘中守护进程")
    parser.add_argument("--once", action="store_true", help="仅执行一轮后退出（用于诊断或测试）")
    parser.add_argument("--interval", type=int, default=60, help="轮询间隔（秒）")
    args = parser.parse_args()

    run_daemon(once=args.once, poll_interval=args.interval)
