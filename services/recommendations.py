"""Read trusted recommendation artifacts and present score metadata.

No HTTP or watchlist storage dependencies. Configuration is supplied by callers.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any
import json
import math
from config.settings import Settings
import pandas as pd

def _determine_bracket(price: float) -> tuple[str, str]:
    if price <= 20.0:
        return "low", "黄金低价 (≤¥20)"
    elif price <= 50.0:
        return "mid", "稳健中价 (¥20-¥50)"
    elif price <= 100.0:
        return "high", "成长中高 (¥50-¥100)"
    else:
        return "top", "百元资产 (>¥100)"


def _build_attribution(settings: Settings, code: str, name: str, price: float, blend: float, lgb: float, linear: float) -> dict[str, Any]:
    """根据因子分值多维度合成归因与推荐逻辑说明。"""
    rationales = [f"综合评分 {blend:.2f}（0–100）。"]
    if lgb is not None:
        rationales.append(f"引擎 A 评分 {lgb:.2f}。")
    if linear is not None:
        rationales.append(f"引擎 B 评分 {linear:.2f}。")
    rationales.append("未提供因子贡献或资金流证据，无法核实具体推荐原因。")
    return {
        "rationale": "；".join(rationales), "rationales": rationales,
        "target_price": round(price * (1 + settings.risk.take_profit), 2),
        "stop_loss_price": round(price * (1 - settings.risk.stop_loss), 2),
        "expected_return": "未预测（止盈价为配置阈值）",
        "holding_period": "未预测", "risk_rating": "未评估", "factors": {},
    }


def load_signals(settings: Settings, *, health: dict | None = None) -> list[dict[str, Any]]:
    from services.result_store import ResultStore
    view = ResultStore(settings.paths.artifact_dir).read_view()
    if view["snapshot"] is not None:
        return [] if view["revocation"] else list(view["snapshot"]["rows"])
    from api.health_cache import health_cache
    from data.health import recommendation_state
    if health is None:
        health = health_cache.get_background(settings)
    if not recommendation_state(settings, health)["ready"]:
        return []
    return read_legacy_rows(settings)


def read_legacy_rows(settings: Settings, *, strict=False) -> list[dict[str, Any]]:
    """Read the legacy artifact without scheduling health work (startup only)."""
    path = Path(settings.paths.artifact_dir) / "recommendations.csv"
    if not path.exists():
        if strict:
            raise ValueError("旧推荐 CSV 缺失")
        return []
    try:
        frame = pd.read_csv(path, dtype={"code": str})
        return format_rows(settings, frame, strict=strict)
    except Exception:
        if strict:
            raise
        return []


def format_rows(settings: Settings, frame: pd.DataFrame, *, strict=True) -> list[dict[str, Any]]:
    """Adapt decision plan rows in memory, preserving optional finite metadata."""
    required = {"code", "price", "score_blend"}
    if not required.issubset(frame.columns):
        if frame.empty and not strict:
            return []
        raise ValueError("推荐缺少 code/price/score_blend 字段")
    if strict:
        # JSON serialisation normalises infinities to null; reject them while
        # they are still distinguishable from legitimately absent extras.
        for value in frame.select_dtypes(include='number').to_numpy().flat:
            if not pd.isna(value) and math.isinf(float(value)):
                raise ValueError('推荐含有非有限数值字段')
    items = []
    for row in json.loads(frame.to_json(orient="records", date_format="iso")):
        code = str(row["code"]).strip().zfill(6)
        if code.startswith("sh000") or code.startswith("sz399") or code == settings.data.index_symbol:
            continue
        try:
            price, blend = float(row["price"]), float(row["score_blend"])
            if not math.isfinite(price) or price <= 0 or not math.isfinite(blend):
                raise ValueError("推荐价格/分数无效")
            engines = []
            for key, alias in (("score_a", "score_lgb"), ("score_b", "score_linear")):
                value = row.get(key, row.get(alias))
                value = float(value) if value is not None else None
                if value is not None and not math.isfinite(value):
                    raise ValueError("推荐引擎分数无效")
                engines.append(value)
        except (ValueError, TypeError):
            if strict:
                raise ValueError("推荐价格/分数无效")
            continue
        name = str(row.get("name") or code)
        bracket, label = _determine_bracket(price)
        item = dict(row, code=code, name=name, price=round(price, 2),
                    score_blend=round(blend, 3), hand_cost=round(price*100, 2),
                    price_bracket=bracket, bracket_label=label,
                    score_lgb=round(engines[0], 3) if engines[0] is not None else None,
                    score_linear=round(engines[1], 3) if engines[1] is not None else None,
                    why_recommended=_build_attribution(settings, code, name, price, blend, *engines))
        for key in ("trade_date", "generated_at", "updated_at"):
            item.setdefault(key, "")
        for key in ("source", "price_source"):
            item.setdefault(key, "unknown")
        for key in ("coverage", "history_coverage"):
            item.setdefault(key, 0.)
        items.append(item)
    items.sort(key=lambda row: (-row["score_blend"], row["code"]))
    for rank, item in enumerate(items, 1):
        item["rank"] = rank
    return items
