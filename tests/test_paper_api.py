"""模拟盘 API、价格档位下单与选股多维归因自动化测试集。"""

from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)


def test_paper_account_init():
    res = client.get("/api/paper/account")
    assert res.status_code == 200
    data = res.json()
    assert "cash" in data
    assert "total_equity" in data
    assert "positions" in data
    assert "orders" in data
    assert isinstance(data["positions"], list)
    assert isinstance(data["orders"], list)


def test_paper_order_buy_and_sell():
    # 1. 重置账户
    client.post("/api/paper/reset")
    acc = client.get("/api/paper/account").json()
    initial_cash = acc["cash"]
    assert initial_cash == 1_000_000.0

    # 2. 买入 100 股茅台 @ 1400.0 (所需 140,000)
    buy_res = client.post(
        "/api/paper/order",
        json={
            "code": "600519",
            "name": "贵州茅台",
            "side": "买入",
            "shares": 100,
            "price": 1400.0,
            "order_type": "买一档 (-0.2%)",
        },
    )
    assert buy_res.status_code == 200
    acc_after_buy = client.get("/api/paper/account").json()
    assert acc_after_buy["cash"] == initial_cash - 140_000.0
    assert len(acc_after_buy["positions"]) == 1
    pos = acc_after_buy["positions"][0]
    assert pos["code"] == "600519"
    assert pos["shares"] == 100
    assert pos["avg_cost"] == 1400.0

    # 3. 卖出 50 股茅台 @ 1500.0
    sell_res = client.post(
        "/api/paper/order",
        json={
            "code": "600519",
            "name": "贵州茅台",
            "side": "卖出",
            "shares": 50,
            "price": 1500.0,
            "order_type": "市价",
        },
    )
    assert sell_res.status_code == 200
    acc_after_sell = client.get("/api/paper/account").json()
    # 现金增加了 50 * 1500 = 75,000
    assert acc_after_sell["cash"] == (initial_cash - 140_000.0) + 75_000.0
    assert len(acc_after_sell["positions"]) == 1
    assert acc_after_sell["positions"][0]["shares"] == 50

    # 4. 异常测试：持仓不足卖出
    fail_sell = client.post(
        "/api/paper/order",
        json={
            "code": "600519",
            "name": "贵州茅台",
            "side": "卖出",
            "shares": 9999,
            "price": 1500.0,
        },
    )
    assert fail_sell.status_code == 400
    assert "持仓不足" in fail_sell.json()["detail"]

    # 5. 异常测试：资金不足买入
    fail_buy = client.post(
        "/api/paper/order",
        json={
            "code": "600519",
            "name": "贵州茅台",
            "side": "买入",
            "shares": 100000,
            "price": 1500.0,
        },
    )
    assert fail_buy.status_code == 400
    assert "可用资金不足" in fail_buy.json()["detail"]


def test_paper_random_portfolio_and_shock():
    # 1. 随机生成持仓
    rand_res = client.post("/api/paper/random-portfolio")
    assert rand_res.status_code == 200
    data = rand_res.json()
    assert len(data["positions"]) > 0
    assert data["cash"] < 1_000_000.0

    # 2. 压力测试（价格随机震荡）
    shock_res = client.post("/api/paper/shock-test")
    assert shock_res.status_code == 200
    shock_data = shock_res.json()
    assert "positions" in shock_data

    # 3. 重置账户
    reset_res = client.post("/api/paper/reset")
    assert reset_res.status_code == 200
    reset_acc = client.get("/api/paper/account").json()
    assert reset_acc["cash"] == 1_000_000.0
    assert len(reset_acc["positions"]) == 0


def test_signals_multi_factor_attribution():
    res = client.get("/api/signals")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) > 0

    first = data[0]
    assert "code" in first
    assert "name" in first
    assert "score_blend" in first
    assert "why_recommended" in first

    attr = first["why_recommended"]
    assert "rationale" in attr
    assert "rationales" in attr
    assert isinstance(attr["rationales"], list)
    assert "factors" in attr
    factors = attr["factors"]
    assert "momentum" in factors
    assert "trend" in factors
    assert "volume" in factors
    assert "model_synergy" in factors
    assert "risk_safety" in factors
    assert "target_price" in attr
    assert "stop_loss_price" in attr
    assert "holding_period" in attr


def test_ai_simulation_plan_and_execute():
    # 1. 模拟重置账户
    client.post("/api/paper/reset")

    # 2. 获取 AI 自主模拟方案
    res = client.get("/api/paper/ai-simulation?risk_pref=balanced")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["risk_pref"] == "balanced"
    assert "recommendations" in data
    assert len(data["recommendations"]) > 0

    first = data["recommendations"][0]
    assert "code" in first
    assert "name" in first
    assert "current_price" in first
    assert "price_range_low" in first
    assert "price_range_high" in first
    assert first["price_range_low"] < first["price_range_high"]
    assert "recommended_shares" in first
    assert first["recommended_shares"] >= 100
    assert first["recommended_shares"] % 100 == 0
    assert "ladder" in first
    assert len(first["ladder"]) == 2
    assert "ai_rationale" in first

    # 3. 执行 AI 一键自主建仓
    exec_res = client.post("/api/paper/ai-simulation/execute", json={"risk_pref": "balanced"})
    assert exec_res.status_code == 200
    exec_data = exec_res.json()
    assert exec_data["status"] == "ai_execution_completed"
    assert exec_data["executed_count"] > 0
    assert len(exec_data["account"]["positions"]) > 0
    assert exec_data["account"]["cash"] < 1_000_000.0


def test_kline_rich_data():
    # 日 K 测试（必须不为空且具备完整 OHLC 数据）
    daily = client.get("/api/kline/daily/600519").json()
    assert len(daily["dates"]) > 0
    assert len(daily["values"]) > 0
    assert len(daily["values"][0]) == 4
    assert len(daily["mas"]["ma5"]) == len(daily["dates"])

    # 1 分钟分时 K 测试
    minute = client.get("/api/kline/minute/600519").json()
    assert len(minute["dates"]) > 0
    assert len(minute["values"]) > 0
    assert len(minute["values"][0]) == 4


def test_price_bracket_signals():
    # 1. 验证价格区间分档统计 API
    stats_res = client.get("/api/signals/stats")
    assert stats_res.status_code == 200
    stats = stats_res.json()
    assert "brackets" in stats
    assert "total_count" in stats
    assert stats["brackets"]["low"]["count"] > 0
    assert stats["brackets"]["low"]["min_hand_cost"] > 0
    assert stats["brackets"]["mid"]["count"] > 0

    # 2. 验证低价股池 (bracket=low) 筛选
    low_res = client.get("/api/signals?bracket=low")
    assert low_res.status_code == 200
    low_stocks = low_res.json()
    assert len(low_stocks) > 0
    for s in low_stocks:
        assert s["price_bracket"] == "low"
        assert s["price"] <= 20.0
        assert s["hand_cost"] == round(s["price"] * 100, 2)

    # 3. 验证自定义价格过滤 (min_price=10, max_price=30)
    range_res = client.get("/api/signals?min_price=10&max_price=30")
    assert range_res.status_code == 200
    range_stocks = range_res.json()
    for s in range_stocks:
        assert 10.0 <= s["price"] <= 30.0

    # 4. 验证一手资金门槛升序排序 (sort_by=hand_cost_asc)
    sort_res = client.get("/api/signals?sort_by=hand_cost_asc")
    assert sort_res.status_code == 200
    sort_stocks = sort_res.json()
    assert len(sort_stocks) > 1
    for i in range(len(sort_stocks) - 1):
        assert sort_stocks[i]["hand_cost"] <= sort_stocks[i + 1]["hand_cost"]



