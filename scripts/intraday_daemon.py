"""盘中守护进程：09:25–15:05 分钟级监控持仓与自选，触发止损止盈(D)、执行择时(C1)、异动提醒(C2)，写入心跳与防休眠。"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
import signal
import subprocess
import time
from pathlib import Path

import pandas as pd
from strategy import risk
from services.decision_results import operation_frame

from config.settings import Settings, load_settings, settings_version
from data import db, fetcher, pipeline
from data.health import assess_data_health, recommendation_state
from data.timeliness import exchange_now, quote_state
from scripts.daemon_control import DaemonLease, DaemonAlreadyRunning
from data.trading_calendar import closure_kind
from strategy import holdings
from strategy.notify import CompositeNotifier, build_notifier


def is_trading_time(now: dt.datetime | None = None) -> bool:
    if now is None:
        now = dt.datetime.now()

    # 周一到周五
    if closure_kind(now.date()) is not None:
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
            cost = risk.positive(h.get("cost", h.get("cost_price")))
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
    if risk.buying_allowed(risk.current_policy(settings)) and orders_df is not None and not orders_df.empty:
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


def load_monitor_inputs(settings: Settings) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """读取真实持仓、自选股和当前指令单；损坏的输入交给守护循环记录错误。"""
    try:
        positions = holdings.load_positions(settings.data.db_path)
        positions = positions.rename(columns={"cost": "cost_price"})
    except Exception as exc:
        raise RuntimeError(f"读取持仓失败: {exc}") from exc

    try:
        with sqlite3.connect(settings.data.db_path) as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_watchlist'"
            ).fetchone()
            watchlist = [r[0] for r in conn.execute("SELECT code FROM user_watchlist")] if exists else []
    except Exception as exc:
        raise RuntimeError(f"读取自选股失败: {exc}") from exc

    order_state = recommendation_state(settings, assess_data_health(settings))
    order_ready = order_state["ready"]
    orders_file = Path(settings.paths.artifact_dir) / "latest_orders.csv"
    try:
        orders = operation_frame(settings, order_state, "orders")
        if not orders.empty and not {"code", "action", "limit_low", "limit_high"}.issubset(orders.columns):
            raise ValueError("指令单缺少必要字段")
        if not order_ready:
            orders.attrs["blocked_reason"] = order_state["reason"]
    except Exception as exc:
        raise RuntimeError(f"读取指令单失败: {exc}") from exc

    codes = set(positions["code"].astype(str).str.zfill(6)) if not positions.empty else set()
    codes.update(str(code).zfill(6) for code in watchlist)
    if not orders.empty:
        codes.update(orders["code"].astype(str).str.zfill(6))
    codes.update(str(code).zfill(6) for code in settings.universe.extra_codes)
    return positions, orders, sorted(codes)


def run_daemon(settings: Settings | None = None, once: bool = False, poll_interval: int = 60,
               *, project_root=None, lease_token=None, lease_fd=None):
    with DaemonLease(project_root or Path.cwd(), lease_token, lease_fd):
        return _run_daemon(settings, once, poll_interval)


def _run_daemon(settings: Settings | None, once: bool, poll_interval: int):
    reload_config = settings is None
    settings = settings if settings is not None else load_settings()
    db.init_db(settings.data.db_path)
    notifier = build_notifier(settings)
    applied_version = settings_version(settings)

    # 启动 macOS 防休眠 (caffeinate)
    caffeinate_proc = None
    if not once:
        try:
            caffeinate_proc = subprocess.Popen(["caffeinate", "-d", "-i", "-s"])
        except Exception:
            pass

    pid = os.getpid()
    last_minute_capture = 0.0
    last_error = ""
    db.record_heartbeat(settings.data.db_path, status="started", message="守护进程已启动", pid=pid, config_version=applied_version)

    try:
        while True:
            now = exchange_now(dt.datetime.now())
            try:
                if reload_config:
                    try:
                        candidate = load_settings()
                        candidate_version = settings_version(candidate)
                        if candidate_version != applied_version:
                            # Construct dependencies before publishing the applied version.
                            candidate_notifier = (build_notifier(candidate)
                                                  if candidate.notify != settings.notify else notifier)
                            if candidate.data.db_path != settings.data.db_path:
                                db.init_db(candidate.data.db_path)
                            settings = candidate
                            notifier = candidate_notifier
                            applied_version = candidate_version
                    except Exception as exc:
                        raise RuntimeError(f"配置重载失败: {exc}") from exc
                if not is_trading_time(now) and not once:
                    db.record_heartbeat(settings.data.db_path, status="standby", message="非交易时段，待机轮询中",
                                        pid=pid, config_version=applied_version)
                    time.sleep(poll_interval)
                    continue
                holdings_df, orders_df, watch_codes = load_monitor_inputs(settings)

                # 批量抓取盘中快照
                spot_df = fetcher.fetch_spot_batch(watch_codes)
                if watch_codes and spot_df.empty:
                    raise RuntimeError(f"盘中行情为空，无法监控 {len(watch_codes)} 只标的")
                if not spot_df.empty:
                    db.save_spot(settings.data.db_path, spot_df)
                    fresh = spot_df.apply(lambda row: not quote_state(row, now=now)["stale"], axis=1)
                    spot_df = spot_df[fresh].copy()
                    if watch_codes and spot_df.empty:
                        raise RuntimeError("盘中行情没有有效新鲜快照，拒绝触发过期价格提醒")
                returned_codes = set(spot_df["code"].astype(str).str.zfill(6)) if not spot_df.empty else set()
                missing_spot = sorted(set(watch_codes) - returned_codes)

                # 判定触发项并分级发送
                triggers = check_intraday_triggers(
                    spot_df=spot_df,
                    orders_df=orders_df,
                    holdings_df=holdings_df,
                    settings=settings,
                    notifier=notifier,
                )

                minute_failed = []
                if not once and watch_codes and time.monotonic() - last_minute_capture >= 1800:
                    minute_result = pipeline.update_minute_bars(
                        settings, codes=watch_codes, date=now.date().isoformat()
                    )
                    minute_failed = minute_result["failed"] + minute_result["empty"]
                    last_minute_capture = time.monotonic()

                msg = f"监控 {len(spot_df)} 只标的，触发 {len(triggers)} 项提醒"
                if minute_failed:
                    msg += f"；分钟线采集失败 {len(minute_failed)} 只: {','.join(minute_failed[:10])}"
                if missing_spot:
                    msg += f"；盘中行情缺失 {len(missing_spot)} 只: {','.join(missing_spot[:10])}"
                if orders_df.attrs.get("blocked_reason"):
                    msg += f"；指令单暂停: {orders_df.attrs['blocked_reason']}"
                db.record_heartbeat(settings.data.db_path,
                                    status="degraded" if minute_failed or missing_spot or orders_df.attrs.get("blocked_reason") else "running",
                                    message=msg, pid=pid, config_version=applied_version)
                last_error = ""

            except Exception as e:
                last_error = f"轮询异常: {e}"
                db.record_heartbeat(settings.data.db_path, status="error", message=last_error, pid=pid, config_version=applied_version)

            if once:
                break
            time.sleep(poll_interval)

    finally:
        db.record_heartbeat(settings.data.db_path, status="stopped",
                            message=(f"守护进程已退出；{last_error}" if last_error else "守护进程已退出"), pid=pid, config_version=applied_version)
        if caffeinate_proc:
            try:
                caffeinate_proc.terminate()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="StockPilot 盘中守护进程")
    parser.add_argument("--once", action="store_true", help="仅执行一轮后退出（用于诊断或测试）")
    parser.add_argument("--interval", type=int, default=60, help="轮询间隔（秒）")
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--lease-token", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--lease-fd", type=int, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()
    os.chdir(args.project_root)
    def stop(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    try:
        run_daemon(once=args.once, poll_interval=args.interval, project_root=args.project_root,
                   lease_token=args.lease_token, lease_fd=args.lease_fd)
    except DaemonAlreadyRunning as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
