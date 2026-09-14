"""StockPilot v2 Streamlit 看板：5 页架构（信号看盘 / 指令单中心 / 持仓与宏观 / 每日复盘 / 守护与设置）。"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config.settings import Settings, load_settings, save_settings
from data import db
from strategy import holdings, paper
from strategy.notify import CompositeNotifier, WebhookNotifier, MacLocalNotifier

st.set_page_config(page_title="StockPilot v2", layout="wide", page_icon="📈")


def format_pct(x: float) -> str:
    return f"{x:.2%}"


def load_rec(path: str = "artifacts/recommendations.csv") -> pd.DataFrame:
    p = Path(path)
    return pd.read_csv(p, dtype={"code": str}) if p.exists() else pd.DataFrame()


def load_orders_csv(path: str = "artifacts/latest_orders.csv") -> pd.DataFrame:
    p = Path(path)
    return pd.read_csv(p, dtype={"code": str}) if p.exists() else pd.DataFrame()


def list_recap_reports(report_dir: str = "artifacts/reports") -> list[str]:
    p = Path(report_dir)
    if not p.exists():
        return []
    files = sorted(p.glob("*.md"), reverse=True)
    return [f.stem for f in files]


def get_daemon_status_indicator(hb: dict | None, max_delay_seconds: int = 180) -> tuple[str, str]:
    """返回 (状态图标与文本, 详细说明)。"""
    if not hb:
        return "🔴 离线未运行", "暂无心跳记录，请在终端执行：python3 -m scripts.intraday_daemon"

    ts_str = hb.get("timestamp", "")
    status = hb.get("status", "unknown")
    pid = hb.get("pid", 0)
    msg = hb.get("message", "")

    try:
        ts = dt.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        # 兼容系统时区
        now_utc = dt.datetime.now(dt.timezone.utc)
        elapsed = (now_utc - ts).total_seconds()
    except Exception:
        elapsed = 9999

    if elapsed <= max_delay_seconds and status in ("running", "started", "standby"):
        return f"🟢 运行中 (PID: {pid})", f"最近心跳: {ts_str} ({int(elapsed)}秒前) | {msg}"
    else:
        return f"🔴 已断开或异常 (PID: {pid})", f"最后心跳: {ts_str} | 状态: {status} | {msg}"


def _equity_chart(path: str, name: str) -> object:
    df = pd.read_csv(path)
    fig = go.Figure()
    fig.add_trace(go.Scatter(y=df["equity"], name=name))
    return fig


@st.fragment(run_every=60)
def tab_signals(settings: Settings) -> None:
    st.caption("⚡ 盘中每分钟自动刷新（st.fragment）")
    rec = load_rec("artifacts/recommendations.csv")
    if rec.empty:
        st.info("💡 暂无选股推荐清单。请在收盘后运行：`python3 scripts/daily_update.py`")
        return
    spot = db.load_spot(settings.data.db_path)
    if not spot.empty:
        rec = rec.drop(columns=["price"], errors="ignore").merge(spot[["code", "price"]], on="code", how="left")
    st.dataframe(rec.sort_values("score_blend", ascending=False), use_container_width=True)


def tab_orders(settings: Settings) -> None:
    st.header("📋 次日操作指令单")
    orders = load_orders_csv("artifacts/latest_orders.csv")
    if orders.empty:
        st.info("💡 当前暂无生效中的指令单。收盘后运行 daily_update.py 将自动生成。")
        return

    col1, col2 = st.columns([4, 1])
    with col1:
        st.caption("提示：开盘若跳空高开 ≥ 9.8% 涨停则放弃执行买入；价格进入区间后手动下单。")
    with col2:
        csv_data = orders.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "⬇️ 导出指令单 CSV",
            data=csv_data,
            file_name=f"stockpilot_orders_{dt.date.today()}.csv",
            mime="text/csv",
        )

    st.dataframe(orders, use_container_width=True)


def tab_holdings_and_macro(settings: Settings) -> None:
    st.header("🛡️ 宏观温度计与仓位闸门")
    # 宏观仪表盘
    macro_df = db.load_macro_history(settings.data.db_path, limit=1)
    if not macro_df.empty:
        latest = macro_df.iloc[0]
        score = float(latest["score"])
        stance = str(latest["stance"]).upper()
        cap_val = 0.8 if stance == "ATTACK" else (0.3 if stance == "DEFENSE" else 0.6)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("宏观档位", stance)
        c2.metric("综合得分", f"{score:+.2f}")
        c3.metric("总仓位上限", f"{int(cap_val * 100)}%")
        c4.metric("买入权限", "严禁新开仓" if stance == "DEFENSE" else "允许正常开仓")
    else:
        st.info("💡 暂无宏观历史打分，收盘后运行 daily_update.py 将自动计算。")

    st.divider()
    st.header("💼 我的持仓与诊断")
    with st.form("position_form"):
        code = st.text_input("股票代码 (如 000001)")
        name = st.text_input("股票名称")
        shares = st.number_input("持仓股数", min_value=0.0, step=100.0)
        cost = st.number_input("持仓成本价", min_value=0.0, step=0.01)
        submitted = st.form_submit_button("保存持仓")
        if submitted and code and name:
            df = pd.DataFrame([{"code": code, "name": name, "shares": shares, "cost": cost}])
            holdings.save_positions(settings.data.db_path, df)
            st.success(f"已保存 {code} {name}")

    rec = load_rec("artifacts/recommendations.csv")
    spot = db.load_spot(settings.data.db_path)
    if spot.empty and not rec.empty:
        spot = rec[["code", "price"]]

    diag = holdings.diagnose_positions(settings.data.db_path, spot, rec, settings)
    if not diag.empty:
        st.dataframe(diag, use_container_width=True)
    for w in holdings.portfolio_warnings(settings.data.db_path, spot, rec, settings):
        st.warning(w)

    st.divider()
    st.subheader("📝 模拟盘账本")
    with st.form("paper_form"):
        p_code = st.text_input("模拟股票代码")
        p_shares = st.number_input("模拟股数", min_value=0.0, step=100.0)
        p_price = st.number_input("成交价格", min_value=0.0, step=0.01)
        side = st.selectbox("方向", ["买入", "卖出"])
        p_submit = st.form_submit_button("记录模拟交易")
        if p_submit and p_code:
            if side == "买入":
                paper.open_paper_order("artifacts/paper.db", p_code, p_shares, p_price)
            else:
                paper.close_paper_order("artifacts/paper.db", p_code, p_shares, p_price)
            st.success("模拟单已入账")

    prices = dict(zip(spot["code"], spot["price"])) if not spot.empty else {}
    st.json(paper.paper_summary("artifacts/paper.db", prices))


def tab_recap(settings: Settings) -> None:
    st.header("📖 每日复盘简报")
    reports = list_recap_reports("artifacts/reports")
    if not reports:
        st.info("💡 暂无历史复盘简报。15:45 盘后运行 daily_update.py 将自动产出报告。")
        return

    selected_date = st.selectbox("选择复盘日期", reports)
    if selected_date:
        report_path = Path("artifacts/reports") / f"{selected_date}.md"
        if report_path.exists():
            content = report_path.read_text(encoding="utf-8")
            st.markdown(content)


def tab_daemon_and_settings(settings: Settings) -> None:
    st.header("⚙️ 守护进程状态与配置中心")

    # 1. 守护进程状态灯
    st.subheader("盘中守护进程监控 (09:25–15:05)")
    hb = db.get_latest_heartbeat(settings.data.db_path)
    status_icon, status_desc = get_daemon_status_indicator(hb)
    st.markdown(f"### {status_icon}")
    st.caption(status_desc)

    st.divider()

    # 2. 通知设置
    st.subheader("通知渠道设置")
    from dataclasses import replace

    cur_notify = settings.notify
    mac_notify = st.checkbox("开启 macOS 本地原生弹窗与声音通知 (无限量、零成本)", value=cur_notify.enable_mac_notify)
    webhook_url = st.text_input("群机器人 Webhook 地址 (支持企业微信/飞书/钉钉)", value=cur_notify.webhook_url)
    webhook_type = st.selectbox(
        "机器人类型",
        ["generic", "wecom", "feishu", "dingtalk"],
        index=["generic", "wecom", "feishu", "dingtalk"].index(cur_notify.webhook_type)
        if cur_notify.webhook_type in ["generic", "wecom", "feishu", "dingtalk"]
        else 0,
    )

    if st.button("🔔 发送测试推送"):
        test_notifier = WebhookNotifier(webhook_url, webhook_type)
        ok = test_notifier.send("StockPilot 测试通知", "恭喜！Webhook 机器人配置成功，可正常接收提醒！", level="P1")
        if mac_notify:
            MacLocalNotifier().send("StockPilot 测试通知", "macOS 本地通知测试通过！", level="P1")
        if ok or mac_notify:
            st.success("测试通知已发送，请检查接收端！")
        else:
            st.error("发送失败，请检查 Webhook URL 是否正确。")

    st.divider()

    # 3. 风控与选股参数
    st.subheader("风控与账户参数")
    cash = st.number_input("账户资金 (元)", value=float(settings.account.cash), min_value=1000.0)
    stop_loss = st.number_input("硬止损阈值 (默认 -5%)", value=float(settings.risk.stop_loss), min_value=0.01, max_value=0.20, step=0.01)
    take_profit = st.number_input("目标止盈阈值 (默认 +15%)", value=float(settings.risk.take_profit), min_value=0.05, max_value=0.50, step=0.01)

    manual = st.checkbox("手动固定双引擎权重", value=settings.engine.manual_override)
    w_a = st.slider("引擎A (LightGBM) 权重", 0.2, 0.8, float(settings.engine.manual_w_a or settings.engine.w_a), 0.01)

    if st.button("💾 保存全部设置"):
        s = replace(
            settings,
            account=replace(settings.account, cash=cash),
            risk=replace(settings.risk, stop_loss=stop_loss, take_profit=take_profit),
            engine=replace(settings.engine, manual_override=manual, manual_w_a=w_a if manual else None),
            notify=replace(settings.notify, enable_mac_notify=mac_notify, webhook_url=webhook_url, webhook_type=webhook_type),
        )
        save_settings(s)
        st.success("设置已成功保存到 user_settings.json！")


def main() -> None:
    settings = load_settings()
    t1, t2, t3, t4, t5 = st.tabs(["⚡ 信号看盘", "📋 指令单", "🛡️ 持仓与宏观", "📖 每日复盘", "⚙️ 守护与设置"])
    with t1:
        tab_signals(settings)
    with t2:
        tab_orders(settings)
    with t3:
        tab_holdings_and_macro(settings)
    with t4:
        tab_recap(settings)
    with t5:
        tab_daemon_and_settings(settings)


if __name__ == "__main__":
    main()
