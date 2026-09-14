# pytest root marker: keeps project root importable when running from stock-pilot/

import pytest

from data import fetcher


@pytest.fixture(autouse=True)
def _offline_fetcher_preference():
    """单元测试固定走东财模拟源（FakeAk），避免触碰真实腾讯/新浪网络。"""
    fetcher._PREFER = "em"
    fetcher._EM_AVAILABLE = True
    yield
