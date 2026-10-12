# pytest root marker: keeps project root importable when running from stock-pilot/

import pytest

from data import db, fetcher
from strategy import notify


@pytest.fixture(autouse=True)
def _offline_fetcher_preference(monkeypatch, tmp_path):
    """每个测试使用独立工作目录，避免改写真实配置、账本或推荐文件。"""
    monkeypatch.chdir(tmp_path)
    db.init_db("data/market.db")
    monkeypatch.setattr(notify.MacLocalNotifier, "send", lambda *args, **kwargs: False)
    def block_real_webhook(*args, **kwargs):
        raise AssertionError("测试不得发送真实 Webhook 通知")
    monkeypatch.setattr(notify.requests, "post", block_real_webhook)
    fetcher._PREFER = "em"
    fetcher._EM_AVAILABLE = True
    yield
