"""AI 历史数据推演与时间加速沙盒 API。"""

from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Body, Query
from pydantic import BaseModel, Field

from strategy.sandbox import (
    SCHEME_PRESETS,
    get_or_create_sandbox,
    run_multi_scheme_comparison,
    seed_historical_market_data,
)

router = APIRouter(prefix="/api/sandbox", tags=["sandbox"])


class InitSandboxRequest(BaseModel):
    scheme: str = Field("balanced", description="推演方案: momentum | value | balanced | custom")
    start_date: str = Field("2023-01-03", description="推演起始日期")
    end_date: str = Field("2024-12-31", description="推演结束日期")
    initial_cash: float = Field(1_000_000.0, description="初始本金")
    custom_config: dict[str, Any] | None = Field(None, description="自定义策略配置参数")
    watchlist_codes: list[str] | None = Field(None, description="自选股标的列表")


class StepSandboxRequest(BaseModel):
    days: int = Field(1, ge=1, le=500, description="步进交易日数")


@router.get("/schemes")
def get_schemes() -> dict[str, Any]:
    """获取系统支持的 AI 选股方案配置与预设说明。"""
    return {
        "schemes": SCHEME_PRESETS
    }


@router.post("/seed")
def seed_market_data(force: bool = Query(False)) -> dict[str, Any]:
    """存入或刷新往期历史行情数据 (2023-01-03 至 2024-12-31 全量日 K 与基准)。"""
    return seed_historical_market_data(force=force)


@router.post("/init")
def init_sandbox(req: InitSandboxRequest) -> dict[str, Any]:
    """初始化一个全新的历史推演沙盒实例。"""
    sb = get_or_create_sandbox(
        scheme=req.scheme,
        start_date=req.start_date,
        end_date=req.end_date,
        initial_cash=req.initial_cash,
        custom_config=req.custom_config,
        watchlist_codes=req.watchlist_codes,
        force_new=True,
    )
    return sb.get_state()


@router.post("/step")
def step_sandbox(req: StepSandboxRequest) -> dict[str, Any]:
    """时间加速向前推进 n 个交易日。"""
    sb = get_or_create_sandbox()
    return sb.step(days_to_step=req.days)


@router.post("/fast-forward")
def fast_forward_sandbox() -> dict[str, Any]:
    """极速完成全周期推演（秒级跑完全部交易日）。"""
    sb = get_or_create_sandbox()
    total = len(sb.trading_days)
    return sb.step(days_to_step=total)


@router.get("/status")
def get_sandbox_status() -> dict[str, Any]:
    """获取当前推演沙盒状态、资产净值走势与绩效指标。"""
    sb = get_or_create_sandbox()
    return sb.get_state()


@router.post("/compare")
def compare_schemes(
    start_date: str = Body("2023-01-03"),
    end_date: str = Body("2024-12-31"),
    initial_cash: float = Body(1_000_000.0),
    custom_config: dict[str, Any] | None = Body(None),
    watchlist_codes: list[str] | None = Body(None),
) -> dict[str, Any]:
    """执行 AI 多方案横向对决（同时推演动量、稳健、双引擎及基准）。"""
    return run_multi_scheme_comparison(
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
        custom_config=custom_config,
        watchlist_codes=watchlist_codes,
    )
