"""双通道通知系统：macOS 系统原生弹窗声音 + 企业微信/飞书/钉钉 Webhook 机器人，分级频控与冷却机制。"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
from typing import Protocol

import requests


class Notifier(Protocol):
    def send(self, title: str, content: str, level: str = "P1") -> bool:
        ...


class MacLocalNotifier:
    """调用 macOS 系统原生 osascript 发送通知，带提示音。"""

    def send(self, title: str, content: str, level: str = "P1") -> bool:
        # P0 采用警报声 Sosumi，其他采用 Glass
        sound = "Sosumi" if level == "P0" else "Glass"
        # 转义单引号和反斜杠
        safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
        safe_content = content.replace("\\", "\\\\").replace('"', '\\"')
        script = f'display notification "{safe_content}" with title "{safe_title}" sound name "{sound}"'
        try:
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
            return True
        except Exception:
            return False


class WebhookNotifier:
    """支持通用、企业微信、飞书、钉钉群 Webhook 机器人。"""

    def __init__(self, webhook_url: str, webhook_type: str = "generic"):
        self.webhook_url = webhook_url.strip()
        self.webhook_type = webhook_type.lower()

    def send(self, title: str, content: str, level: str = "P1") -> bool:
        if not self.webhook_url:
            return False

        headers = {"Content-Type": "application/json; charset=utf-8"}
        full_text = f"[{level}] {title}\n\n{content}"

        if self.webhook_type in ("wecom", "wework"):
            payload = {"msgtype": "text", "text": {"content": full_text}}
        elif self.webhook_type in ("feishu", "lark"):
            payload = {"msg_type": "text", "content": {"text": full_text}}
        elif self.webhook_type == "dingtalk":
            payload = {"msgtype": "text", "text": {"content": full_text}}
        else:
            payload = {"title": title, "content": content, "level": level}

        try:
            r = requests.post(self.webhook_url, json=payload, headers=headers, timeout=10)
            return r.status_code == 200
        except Exception:
            return False


class MockNotifier:
    """Mock 通道：用于测试与无外部配置时的本地记录。"""

    def __init__(self):
        self.sent_messages: list[dict] = []

    def send(self, title: str, content: str, level: str = "P1") -> bool:
        self.sent_messages.append({"title": title, "content": content, "level": level, "ts": dt.datetime.now()})
        return True


class CompositeNotifier:
    """
    组合通知器：
    - 支持按配置选择 local、webhook、both、mock
    - P0: 紧急（止损止盈），无冷却，立即双通道齐发
    - P1: 普通指令/异动，支持 cooldown 去重（去重键由调用方传入或基于标题）
    - P2: 每日收盘摘要
    - 每日发送总量上限防护（daily_cap）
    """

    def __init__(
        self,
        local_notifier: Notifier | None = None,
        webhook_notifier: Notifier | None = None,
        mock_notifier: MockNotifier | None = None,
        mode: str = "local",
        p1_cooldown_minutes: int = 15,
        daily_cap: int = 30,
    ):
        self.local_notifier = local_notifier or MacLocalNotifier()
        self.webhook_notifier = webhook_notifier
        self.mock_notifier = mock_notifier
        self.mode = mode
        self.p1_cooldown_minutes = p1_cooldown_minutes
        self.daily_cap = daily_cap

        self.last_sent: dict[str, dt.datetime] = {}
        self.daily_count = 0
        self.today_date = dt.date.today()

    def _check_daily_cap(self) -> bool:
        today = dt.date.today()
        if today != self.today_date:
            self.today_date = today
            self.daily_count = 0
        return self.daily_count < self.daily_cap

    def send(self, title: str, content: str, level: str = "P1", key: str | None = None) -> bool:
        if not self._check_daily_cap() and level != "P0":
            # 超出每日上限后，降级只允许 P0 止损止盈
            return False

        # 冷却控制 (P0 不受冷却限制)
        now = dt.datetime.now()
        dedup_key = key or title
        if level == "P1" and dedup_key in self.last_sent:
            elapsed = (now - self.last_sent[dedup_key]).total_seconds() / 60.0
            if elapsed < self.p1_cooldown_minutes:
                return False

        success = False

        if self.mode == "mock" and self.mock_notifier:
            success = self.mock_notifier.send(title, content, level)
        else:
            if self.mode in ("local", "both") and self.local_notifier:
                local_ok = self.local_notifier.send(title, content, level)
                success = success or local_ok
            if self.mode in ("webhook", "both") and self.webhook_notifier:
                webhook_ok = self.webhook_notifier.send(title, content, level)
                success = success or webhook_ok

        if success:
            self.daily_count += 1
            self.last_sent[dedup_key] = now

        return success


def build_notifier(settings) -> CompositeNotifier:
    notify_cfg = getattr(settings, "notify", None)
    if not notify_cfg:
        return CompositeNotifier(mode="local")

    mode = notify_cfg.backend
    local_notif = MacLocalNotifier() if notify_cfg.enable_mac_notify else None
    webhook_notif = (
        WebhookNotifier(notify_cfg.webhook_url, notify_cfg.webhook_type)
        if notify_cfg.webhook_url
        else None
    )

    return CompositeNotifier(
        local_notifier=local_notif,
        webhook_notifier=webhook_notif,
        mock_notifier=MockNotifier() if mode == "mock" else None,
        mode=mode,
        p1_cooldown_minutes=notify_cfg.p1_cooldown_minutes,
        daily_cap=notify_cfg.daily_cap,
    )
