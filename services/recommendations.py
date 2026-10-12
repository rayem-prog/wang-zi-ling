"""Read trusted recommendation artifacts and present score metadata.

No HTTP or watchlist storage dependencies. Configuration is supplied by callers.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any
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
    from api.health_cache import health_cache
    from data.health import recommendation_state
    if health is None:
        health = health_cache.get_background(settings)
    if not recommendation_state(settings, health)["ready"]:
        return []
    rec_path = Path(settings.paths.artifact_dir) / "recommendations.csv"
    rec = pd.DataFrame()

    if rec_path.exists():
        try:
            rec = pd.read_csv(rec_path, dtype={"code": str})
        except Exception:
            rec = pd.DataFrame()

    items = []
    if not rec.empty:
        rec["code"] = rec["code"].astype(str).str.zfill(6)
        if "score_blend" in rec.columns:
            rec = rec.sort_values("score_blend", ascending=False)

        for _, r in rec.iterrows():
            code = str(r["code"]).strip()
            if code.startswith("sh000") or code.startswith("sz399") or code == "000300":
                continue
            name = str(r.get("name", code))
            raw_p = r.get("price", 0.0)
            try:
                price = float(raw_p)
                if pd.isna(price) or price <= 0:
                    continue
            except (ValueError, TypeError):
                continue

            blend = float(r.get("score_blend", float("nan")))
            if pd.isna(blend):
                continue
            lgb = r.get("score_a", r.get("score_lgb"))
            lgb = float(lgb) if pd.notna(lgb) else None
            linear = r.get("score_b", r.get("score_linear"))
            linear = float(linear) if pd.notna(linear) else None
            b_key, b_label = _determine_bracket(price)
            hand_cost = round(price * 100, 2)

            item = {
                "code": code,
                "name": name,
                "price": round(price, 2),
                "hand_cost": hand_cost,
                "price_bracket": b_key,
                "bracket_label": b_label,
                "score_blend": round(blend, 3),
                "score_lgb": round(lgb, 3) if lgb is not None else None,
                "score_linear": round(linear, 3) if linear is not None else None,
                "trade_date": str(r.get("trade_date", "")),
                "source": str(r.get("source", "unknown")),
                "generated_at": str(r.get("generated_at", "")),
                "updated_at": str(r.get("updated_at", "")),
                "price_source": str(r.get("price_source", "unknown")),
                "coverage": float(r.get("coverage", 0.0)),
                "history_coverage": float(r.get("history_coverage", 0.0)),
                "why_recommended": _build_attribution(settings, code, name, price, blend, lgb, linear),
            }
            items.append(item)

    return items
