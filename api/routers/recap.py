"""每日复盘简报阅读器 API。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/recap", tags=["recap"])


@router.get("/list")
def list_recap_reports() -> list[str]:
    report_dir = Path("artifacts/reports")
    if not report_dir.exists():
        return []
    files = sorted(report_dir.glob("*.md"), reverse=True)
    return [f.stem for f in files]


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
