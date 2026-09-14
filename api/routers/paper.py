"""模拟交易账户、价格档位下单与持仓随机化 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from strategy import paper

router = APIRouter(prefix="/api/paper", tags=["paper"])
PAPER_DB_PATH = "artifacts/paper.db"


class PaperOrderRequest(BaseModel):
    code: str
    name: str = ""
    side: str  # "买入" 或 "卖出"
    shares: float
    price: float
    order_type: str = "市价"


@router.get("/account")
def get_account() -> dict[str, Any]:
    return paper.get_paper_account(PAPER_DB_PATH)


@router.post("/order")
def place_order(req: PaperOrderRequest) -> dict[str, Any]:
    try:
        res = paper.execute_paper_order(
            path=PAPER_DB_PATH,
            code=req.code,
            name=req.name or req.code,
            side=req.side,
            shares=req.shares,
            price=req.price,
            order_type=req.order_type,
        )
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"模拟下单失败: {e}")


@router.post("/random-portfolio")
def make_random_portfolio() -> dict[str, Any]:
    try:
        return paper.generate_random_portfolio(PAPER_DB_PATH)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成随机持仓失败: {e}")


@router.post("/shock-test")
def shock_portfolio() -> dict[str, Any]:
    try:
        paper.apply_random_price_shock(PAPER_DB_PATH)
        return paper.get_paper_account(PAPER_DB_PATH)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"压力测试失败: {e}")


@router.post("/reset")
def reset_account() -> dict[str, Any]:
    try:
        return paper.reset_paper_account(PAPER_DB_PATH)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重置模拟账户失败: {e}")


class AiExecuteRequest(BaseModel):
    risk_pref: str = "balanced"
    recommendations: list[dict[str, Any]] | None = None


@router.get("/ai-simulation")
def get_ai_simulation(risk_pref: str = "balanced") -> dict[str, Any]:
    try:
        return paper.generate_ai_trading_plan(PAPER_DB_PATH, risk_pref=risk_pref)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 自主模拟推演失败: {e}")


@router.post("/ai-simulation/execute")
def execute_ai_simulation(req: AiExecuteRequest | None = None) -> dict[str, Any]:
    try:
        risk_pref = req.risk_pref if req else "balanced"
        recs = req.recommendations if req else None
        return paper.execute_ai_trading_plan(PAPER_DB_PATH, recommendations=recs, risk_pref=risk_pref)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 自主建仓模拟执行失败: {e}")
