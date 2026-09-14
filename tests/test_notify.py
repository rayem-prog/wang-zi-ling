import datetime as dt
import pytest

from strategy import notify


def test_mock_notifier():
    mock = notify.MockNotifier()
    assert mock.send("Title", "Content", level="P0") is True
    assert len(mock.sent_messages) == 1
    assert mock.sent_messages[0]["level"] == "P0"


def test_composite_cooldown():
    mock = notify.MockNotifier()
    comp = notify.CompositeNotifier(
        mock_notifier=mock,
        mode="mock",
        p1_cooldown_minutes=15,
        daily_cap=10,
    )

    # 1. P1 first send succeeds
    assert comp.send("600519 买入", "价格到达区间", level="P1", key="600519:buy") is True
    assert len(mock.sent_messages) == 1

    # 2. Immediate second P1 with same key fails due to cooldown
    assert comp.send("600519 买入", "价格到达区间", level="P1", key="600519:buy") is False
    assert len(mock.sent_messages) == 1

    # 3. P1 with different key succeeds
    assert comp.send("000001 买入", "价格到达区间", level="P1", key="000001:buy") is True
    assert len(mock.sent_messages) == 2

    # 4. P0 with same key is NOT blocked by cooldown
    assert comp.send("600519 止损", "触及止损线", level="P0", key="600519:buy") is True
    assert len(mock.sent_messages) == 3


def test_daily_cap_blocks_p1_allows_p0():
    mock = notify.MockNotifier()
    comp = notify.CompositeNotifier(
        mock_notifier=mock,
        mode="mock",
        p1_cooldown_minutes=0,
        daily_cap=2,
    )

    # Send 2 messages to hit cap
    assert comp.send("Msg 1", "Content", level="P1", key="k1") is True
    assert comp.send("Msg 2", "Content", level="P1", key="k2") is True
    assert comp.daily_count == 2

    # 3rd P1 message blocked by daily cap
    assert comp.send("Msg 3", "Content", level="P1", key="k3") is False

    # P0 emergency still allowed even when daily cap exceeded
    assert comp.send("Emergency Stop Loss", "Stop now", level="P0", key="k4") is True
    assert len(mock.sent_messages) == 3


def test_webhook_notifier_formats(monkeypatch):
    posted_payloads = []

    class FakeResponse:
        status_code = 200

    def fake_post(url, json=None, headers=None, timeout=10):
        posted_payloads.append((url, json))
        return FakeResponse()

    monkeypatch.setattr(notify.requests, "post", fake_post)

    # WeCom
    wecom = notify.WebhookNotifier("https://qyapi.weixin.qq.com/test", webhook_type="wecom")
    wecom.send("Alert", "Test msg", level="P0")
    assert posted_payloads[-1][1]["msgtype"] == "text"
    assert "[P0] Alert" in posted_payloads[-1][1]["text"]["content"]

    # Feishu
    feishu = notify.WebhookNotifier("https://open.feishu.cn/test", webhook_type="feishu")
    feishu.send("Alert", "Test msg", level="P1")
    assert posted_payloads[-1][1]["msg_type"] == "text"
    assert "[P1] Alert" in posted_payloads[-1][1]["content"]["text"]
