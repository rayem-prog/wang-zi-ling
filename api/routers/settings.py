"""配置管理、通知测试与守护进程控制 API。"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from dataclasses import asdict, replace
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from config.settings import Settings, load_settings, save_settings
from data import db
from strategy.notify import MacLocalNotifier, WebhookNotifier

router = APIRouter(prefix="/api/settings", tags=["settings"])

_DAEMON_PROCESS: subprocess.Popen | None = None


class UpdateSettingsRequest(BaseModel):
    cash: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    manual_override: bool | None = None
    manual_w_a: float | None = None
    enable_mac_notify: bool | None = None
    webhook_url: str | None = None
    webhook_type: str | None = None


class TestNotifyRequest(BaseModel):
    webhook_url: str
    webhook_type: str
    enable_mac_notify: bool = False


@router.get("")
def get_current_settings() -> dict[str, Any]:
    s = load_settings()
    return asdict(s)


@router.post("")
def update_settings(payload: UpdateSettingsRequest) -> dict[str, Any]:
    s = load_settings()

    new_account = s.account
    if payload.cash is not None:
        new_account = replace(new_account, cash=payload.cash)

    new_risk = s.risk
    if payload.stop_loss is not None:
        new_risk = replace(new_risk, stop_loss=payload.stop_loss)
    if payload.take_profit is not None:
        new_risk = replace(new_risk, take_profit=payload.take_profit)

    new_engine = s.engine
    if payload.manual_override is not None:
        new_engine = replace(
            new_engine,
            manual_override=payload.manual_override,
            manual_w_a=payload.manual_w_a if payload.manual_override else None,
        )

    new_notify = s.notify
    if payload.enable_mac_notify is not None:
        new_notify = replace(new_notify, enable_mac_notify=payload.enable_mac_notify)
    if payload.webhook_url is not None:
        new_notify = replace(new_notify, webhook_url=payload.webhook_url)
    if payload.webhook_type is not None:
        new_notify = replace(new_notify, webhook_type=payload.webhook_type)

    updated_settings = replace(
        s,
        account=new_account,
        risk=new_risk,
        engine=new_engine,
        notify=new_notify,
    )
    save_settings(updated_settings)
    return {"status": "success", "settings": asdict(updated_settings)}


@router.post("/notify/test")
def test_notification(req: TestNotifyRequest) -> dict[str, Any]:
    notifier = WebhookNotifier(req.webhook_url, req.webhook_type)
    webhook_ok = notifier.send("StockPilot 测试通知", "Webhook 机器人配置成功，可正常接收提醒！", level="P1")
    mac_ok = True
    if req.enable_mac_notify:
        mac_ok = MacLocalNotifier().send("StockPilot 测试通知", "macOS 本地通知测试通过！", level="P1")

    return {
        "webhook_ok": webhook_ok,
        "mac_ok": mac_ok,
        "message": "测试通知已触发",
    }


@router.post("/daemon/start")
def start_daemon() -> dict[str, Any]:
    global _DAEMON_PROCESS
    settings = load_settings()
    hb = db.get_latest_heartbeat(settings.data.db_path)

    # 检查当前是否已有进程存活
    if _DAEMON_PROCESS and _DAEMON_PROCESS.poll() is None:
        return {"status": "already_running", "pid": _DAEMON_PROCESS.pid}

    # 启动新进程
    cmd = [sys.executable, "-m", "scripts.intraday_daemon"]
    _DAEMON_PROCESS = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=os.getcwd(),
    )
    return {"status": "started", "pid": _DAEMON_PROCESS.pid}


@router.post("/daemon/stop")
def stop_daemon() -> dict[str, Any]:
    global _DAEMON_PROCESS
    killed_pids = []
    if _DAEMON_PROCESS and _DAEMON_PROCESS.poll() is None:
        _DAEMON_PROCESS.terminate()
        try:
            _DAEMON_PROCESS.wait(timeout=3)
        except Exception:
            _DAEMON_PROCESS.kill()
        killed_pids.append(_DAEMON_PROCESS.pid)
        _DAEMON_PROCESS = None

    # 同时尝试杀死 DB 中记录的 PID（如果它是由终端启动的）
    settings = load_settings()
    hb = db.get_latest_heartbeat(settings.data.db_path)
    if hb and hb.get("pid"):
        db_pid = hb["pid"]
        if db_pid not in killed_pids and db_pid > 1:
            try:
                os.kill(db_pid, signal.SIGTERM)
                killed_pids.append(db_pid)
            except Exception:
                pass

    return {"status": "stopped", "killed_pids": killed_pids}
