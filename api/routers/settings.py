"""配置管理、通知测试与守护进程控制 API。"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from dataclasses import asdict, replace
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel

from config.settings import Settings, load_settings, save_settings, settings_version
from data import db
from strategy.notify import MacLocalNotifier, WebhookNotifier

router = APIRouter(prefix="/api/settings", tags=["settings"])


class UpdateSettingsRequest(BaseModel):
    cash: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    manual_override: bool | None = None
    manual_w_a: float | None = None
    enable_mac_notify: bool | None = None
    webhook_url: str | None = None
    webhook_type: str | None = None
    notify_backend: Literal['local', 'webhook', 'both', 'mock'] | None = None


class TestNotifyRequest(BaseModel):
    webhook_url: str
    webhook_type: str
    enable_mac_notify: bool = False


@router.get("")
def get_current_settings() -> dict[str, Any]:
    s = load_settings()
    return dict(asdict(s), config_version=settings_version(s))


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
    if payload.notify_backend is not None:
        new_notify = replace(new_notify, backend=payload.notify_backend)
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
    return {"status": "success", "settings": asdict(updated_settings),
            "config_version": settings_version(updated_settings)}


@router.post("/notify/test")
def test_notification(req: TestNotifyRequest) -> dict[str, Any]:
    notifier = WebhookNotifier(req.webhook_url, req.webhook_type)
    webhook_ok = notifier.send("StockPilot 测试通知", "Webhook 通道连通测试", level="P1") if req.webhook_url.strip() else None
    mac_ok = None
    if req.enable_mac_notify:
        mac_ok = MacLocalNotifier().send("StockPilot 测试通知", "macOS 本地通知测试通过！", level="P1")

    return {
        "webhook_ok": webhook_ok,
        "mac_ok": mac_ok,
        "success": bool(webhook_ok or mac_ok),
        "channels": {"local": 'skipped' if mac_ok is None else 'sent' if mac_ok else 'failed',
                     "webhook": 'skipped' if webhook_ok is None else 'sent' if webhook_ok else 'failed'},
        "message": "至少一个渠道发送成功" if webhook_ok or mac_ok else "未发送成功；请检查渠道或配置",
    }


@router.post("/daemon/start")
def start_daemon() -> dict[str, Any]:
    from scripts import daemon_control
    return daemon_control.start_daemon()


@router.post("/daemon/stop")
def stop_daemon() -> dict[str, Any]:
    from scripts import daemon_control
    return daemon_control.stop_daemon()
