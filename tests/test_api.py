"""FastAPI 全量接口自动化测试集。"""

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_status_endpoint():
    res = client.get("/api/status")
    assert res.status_code == 200
    data = res.json()
    assert "server_time" in data
    assert "market" in data
    assert "daemon" in data
    assert "macro" in data
    assert "counts" in data


def test_signals_endpoint():
    res = client.get("/api/signals")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_orders_endpoint():
    res = client.get("/api/orders")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_macro_endpoint():
    res = client.get("/api/macro")
    assert res.status_code == 200
    data = res.json()
    assert "current" in data
    assert "event_status" in data
    assert "history" in data
    assert "score" in data["current"]


def test_holdings_and_paper_endpoints():
    # 1. 查询持仓
    res = client.get("/api/holdings")
    assert res.status_code == 200
    data = res.json()
    assert "positions" in data
    assert "diagnostics" in data
    assert "warnings" in data

    # 2. 录入持仓
    save_res = client.post(
        "/api/holdings",
        json={"code": "000001", "name": "平安银行", "shares": 1000.0, "cost": 10.5},
    )
    assert save_res.status_code == 200

    # 3. 模拟盘状态
    paper_res = client.get("/api/holdings/paper")
    assert paper_res.status_code == 200

    # 4. 删除测试持仓
    del_res = client.delete("/api/holdings/000001")
    assert del_res.status_code == 200


def test_recap_endpoints():
    res = client.get("/api/recap/list")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_kline_endpoints():
    daily_res = client.get("/api/kline/daily/000001")
    assert daily_res.status_code == 200
    daily_data = daily_res.json()
    assert "dates" in daily_data
    assert "values" in daily_data
    assert "volumes" in daily_data
    assert "mas" in daily_data

    min_res = client.get("/api/kline/minute/000001")
    assert min_res.status_code == 200
    min_data = min_res.json()
    assert "dates" in min_data
    assert "values" in min_data


def test_settings_endpoints():
    # 查询配置
    res = client.get("/api/settings")
    assert res.status_code == 200
    data = res.json()
    assert "account" in data
    assert "risk" in data

    # 更新配置
    up_res = client.post(
        "/api/settings",
        json={"cash": 120000.0, "stop_loss": 0.05, "take_profit": 0.15},
    )
    assert up_res.status_code == 200
    assert up_res.json()["settings"]["risk"]["stop_loss"] == 0.05

    # 测试通知接口（使用 mock）
    test_notify_res = client.post(
        "/api/settings/notify/test",
        json={"webhook_url": "https://example.com/mock", "webhook_type": "generic", "enable_mac_notify": False},
    )
    assert test_notify_res.status_code == 200

def test_frontend_dist_served():
    res = client.get("/")
    assert res.status_code == 200
    assert "StockPilot" in res.text

def test_stock_search_endpoint():
    # 测试代码搜索
    res = client.get("/api/stock/search?q=000001")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    if data:
        assert "code" in data[0]
        assert "name" in data[0]

    # 测试拼音首字母搜索
    res_pinyin = client.get("/api/stock/search?q=payh")
    assert res_pinyin.status_code == 200
    assert isinstance(res_pinyin.json(), list)


def test_intraday_decisions_endpoint():
    res = client.get("/api/intraday/decisions")
    assert res.status_code == 200
    data = res.json()
    assert "holdings_decisions" in data
    assert "orders_triggers" in data
    assert "summary" in data


def test_intraday_radar_endpoint():
    res = client.get("/api/intraday/radar")
    assert res.status_code == 200
    assert isinstance(res.json(), list)
