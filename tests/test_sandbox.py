"""AI 历史数据推演沙盒与自选股管理自动化测试集。"""

from fastapi.testclient import TestClient

from api.main import app
from strategy.sandbox import (
    HistoricalReplaySandbox,
    get_or_create_sandbox,
    run_multi_scheme_comparison,
    seed_historical_market_data,
)

client = TestClient(app)


def test_seed_historical_data():
    res = seed_historical_market_data(force=False)
    assert "status" in res
    assert res["total_bars"] > 1000
    assert res["stock_count"] >= 19
    assert res["min_date"] <= "2023-01-05"
    assert res["max_date"] >= "2024-12-25"


def test_sandbox_stepping_and_trades():
    sb = HistoricalReplaySandbox(
        scheme="momentum",
        start_date="2023-01-03",
        end_date="2024-12-31",
        initial_cash=1_000_000.0,
    )
    assert len(sb.trading_days) > 400
    assert sb.current_idx == 0

    # 步进 10 天
    state1 = sb.step(days_to_step=10)
    assert state1["current_step"] == 10
    assert len(state1["equity_curve"]) == 10
    assert state1["total_equity"] > 0
    assert len(state1["positions"]) > 0

    # 步进 50 天
    state2 = sb.step(days_to_step=50)
    assert state2["current_step"] == 60
    assert len(state2["equity_curve"]) == 60
    assert "metrics" in state2
    assert "total_return_pct" in state2["metrics"]
    assert "alpha_pct" in state2["metrics"]
    assert "max_drawdown_pct" in state2["metrics"]
    assert "win_rate_pct" in state2["metrics"]


def test_multi_scheme_comparison():
    res = run_multi_scheme_comparison(
        start_date="2023-01-03",
        end_date="2023-06-30",
        initial_cash=1_000_000.0,
    )
    assert "curves" in res
    assert "momentum" in res["curves"]
    assert "value" in res["curves"]
    assert "balanced" in res["curves"]
    assert "benchmark_curve" in res
    assert len(res["benchmark_curve"]) > 100
    assert "summary" in res
    assert res["summary"]["momentum"]["total_return_pct"] is not None


def test_sandbox_api_endpoints():
    # 1. 方案列表
    schemes_res = client.get("/api/sandbox/schemes")
    assert schemes_res.status_code == 200
    assert "momentum" in schemes_res.json()["schemes"]
    assert "value" in schemes_res.json()["schemes"]

    # 2. 存入往期数据
    seed_res = client.post("/api/sandbox/seed?force=false")
    assert seed_res.status_code == 200
    assert seed_res.json()["total_bars"] > 1000

    # 3. 初始化推演
    init_res = client.post(
        "/api/sandbox/init",
        json={
            "scheme": "value",
            "start_date": "2023-01-03",
            "end_date": "2024-12-31",
            "initial_cash": 500_000.0,
        },
    )
    assert init_res.status_code == 200
    init_data = init_res.json()
    assert init_data["scheme"] == "value"
    assert init_data["initial_cash"] == 500_000.0

    # 4. 时间加速单步步进
    step_res = client.post("/api/sandbox/step", json={"days": 5})
    assert step_res.status_code == 200
    assert step_res.json()["current_step"] == 5

    # 5. 查询状态
    status_res = client.get("/api/sandbox/status")
    assert status_res.status_code == 200
    assert status_res.json()["current_step"] == 5

    # 6. 极速全周期秒级推演
    ff_res = client.post("/api/sandbox/fast-forward")
    assert ff_res.status_code == 200
    assert ff_res.json()["is_finished"] is True
    assert ff_res.json()["progress_pct"] == 100.0


def test_watchlist_api_endpoints():
    # 1. 添加自选股
    add_res = client.post("/api/signals/watchlist", json={"code": "600519", "name": "贵州茅台"})
    assert add_res.status_code == 200
    assert add_res.json()["status"] == "success"

    # 2. 查询自选股列表
    get_res = client.get("/api/signals/watchlist")
    assert get_res.status_code == 200
    wl = get_res.json()
    assert any(s["code"] == "600519" for s in wl)

    # 3. 按 codes 查询选股
    filter_res = client.get("/api/signals?codes=600519,000725")
    assert filter_res.status_code == 200
    codes = [s["code"] for s in filter_res.json()]
    assert "600519" in codes

    # 4. 移除自选股
    del_res = client.delete("/api/signals/watchlist/600519")
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "success"
