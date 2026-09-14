import pandas as pd

from config.settings import Settings
from strategy import holdings


def test_style_keywords():
    assert holdings.classify_style("人工智能ETF") == "题材"
    assert holdings.classify_style("贵州茅台") == "价值"


def test_diagnose_actions(tmp_path):
    db_path = str(tmp_path / "p.db")
    positions = pd.DataFrame(
        [{"code": "000001", "name": "平安银行", "shares": 1000, "cost": 9.0, "style": "价值"}]
    )
    holdings.save_positions(db_path, positions)
    quotes = pd.DataFrame(
        [{"code": "000001", "name": "平安银行", "price": 12.0, "pct_change": 1.0}]
    )
    rec = pd.DataFrame(
        [{"code": "000001", "score_blend": 85.0, "rating": "强烈看多"}]
    )
    out = holdings.diagnose_positions(db_path, quotes, rec, Settings())
    assert out.iloc[0]["pnl_pct"] > 0
    assert out.iloc[0]["action"] == "加仓"


def test_concentration_warning(tmp_path):
    db_path = str(tmp_path / "p.db")
    positions = pd.DataFrame(
        [
            {"code": "600001", "name": "AI机器人", "shares": 8000, "cost": 10.0, "style": "题材"},
            {"code": "600002", "name": "芯片龙头", "shares": 8000, "cost": 10.0, "style": "题材"},
        ]
    )
    holdings.save_positions(db_path, positions)
    quotes = pd.DataFrame(
        [
            {"code": "600001", "name": "AI机器人", "price": 12.0},
            {"code": "600002", "name": "芯片龙头", "price": 12.0},
        ]
    )
    rec = pd.DataFrame({"code": [], "score_blend": [], "rating": []})
    warns = holdings.portfolio_warnings(db_path, quotes, rec, Settings(account=Settings().account.__class__(cash=80_000.0)))
    assert any("题材" in w for w in warns)
