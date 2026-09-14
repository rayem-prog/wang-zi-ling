"""每日复盘简报阅读器与全流程算法优化调度 API。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from blend import weights as bw
from config.settings import load_settings
from scripts import daily_update

router = APIRouter(prefix="/api/recap", tags=["recap"])


@router.get("/list")
def list_recap_reports() -> list[str]:
    report_dir = Path("artifacts/reports")
    if not report_dir.exists():
        return []
    files = sorted(report_dir.glob("*.md"), reverse=True)
    return [f.stem for f in files]


@router.get("/status/summary")
def get_recap_status_summary() -> dict[str, Any]:
    settings = load_settings()
    report_dir = Path("artifacts/reports")
    files = sorted(report_dir.glob("*.md"), reverse=True) if report_dir.exists() else []
    dates = [f.stem for f in files]

    wa, wb = bw.current_weights(settings.engine, settings.data.db_path)

    # 历史权重调整轨迹 (近 10 次)
    try:
        hist_df = bw.load_weight_history(settings.data.db_path)
        history = hist_df.tail(10).to_dict(orient="records") if not hist_df.empty else []
    except Exception:
        history = []

    return {
        "latest_date": dates[0] if dates else None,
        "total_reports": len(dates),
        "dates": dates,
        "current_weights": {
            "weight_a": wa,
            "weight_b": wb,
        },
        "history": history,
    }


@router.post("/run")
async def run_daily_recap(fast_mode: bool = Query(True, description="是否使用本地极速模式")) -> dict[str, Any]:
    """
    立即执行每日复盘全流程：
    更新全市场行情 -> 计算多因子 -> 评估今日真实收益与超额 -> 动态自适应调整权重 -> 优化多因子公式与LightGBM模型 -> 生成最新复盘简报。
    """
    settings = load_settings()
    try:
        res = await run_in_threadpool(daily_update.run_daily, settings=settings, fast_mode=fast_mode)
        last_date = res.get("last_date")
        report_path = Path("artifacts/reports") / f"{last_date}.md"
        content = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
        return {
            "success": True,
            "date": last_date,
            "result": res,
            "content": content,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"执行每日复盘失败: {e}")


@router.get("/{date}")
def get_recap_report(date: str) -> dict[str, Any]:
    # 防路径穿越
    safe_date = Path(date).name
    report_path = Path("artifacts/reports") / f"{safe_date}.md"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail=f"未找到 {date} 的复盘报告")

    content = report_path.read_text(encoding="utf-8")
    return {
        "date": safe_date,
        "content": content,
    }
