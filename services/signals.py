"""Selection, summaries and personal watchlist use cases (no HTTP types)."""
from __future__ import annotations
from typing import Any
from config.settings import Settings
from data.watchlist import WatchlistRepository
from services.recommendations import load_signals


def list_signals(settings: Settings, *, bracket="all", min_price=None, max_price=None,
                 sort_by="score", codes=None) -> list[dict[str, Any]]:
    items = load_signals(settings)

    # 0. 指定代码集合筛选 (用于自选股等)
    if codes:
        code_set = {c.strip() for c in codes.split(",") if c.strip()}
        items = [it for it in items if it.get("code") in code_set]

    # 1. 价格区间分类筛选
    if bracket and bracket != "all":
        items = [it for it in items if it.get("price_bracket") == bracket]

    # 2. 自定义数值区间筛选
    if min_price is not None:
        items = [it for it in items if it.get("price", 0) >= min_price]
    if max_price is not None:
        items = [it for it in items if it.get("price", 0) <= max_price]

    # 3. 排序
    if sort_by == "price_asc":
        items.sort(key=lambda x: (x.get("price") is None, x.get("price", 0.0)))
    elif sort_by == "price_desc":
        items.sort(key=lambda x: (x.get("price") is None, x.get("price", 0.0)), reverse=True)
    elif sort_by == "hand_cost_asc":
        items.sort(key=lambda x: (x.get("hand_cost") is None, x.get("hand_cost", 0.0)))
    else:  # score
        items.sort(key=lambda x: (x.get("score_blend") is None, x.get("score_blend", 0.0)), reverse=True)

    return items


def list_watchlist(settings: Settings) -> list[dict[str, Any]]:
    """Keep unavailable personal symbols visible without inventing market values."""
    watchlist_map = {r["code"]: r for r in WatchlistRepository(settings.data.db_path).list()}
    if not watchlist_map:
        return []

    all_signals = {s["code"]: s for s in load_signals(settings)}
    results = []

    for code, info in watchlist_map.items():
        item = dict(all_signals.get(code) or {
            "code": code,
            "name": info["name"] or code,
            "price": None,
            "hand_cost": None,
            "score_blend": None,
            "score_lgb": None,
            "score_linear": None,
            "unavailable": True,
        })
        item["is_watchlist"] = True
        item["added_at"] = info["added_at"]
        results.append(item)

    return results


def add_to_watchlist(settings: Settings, payload: dict[str, str]) -> dict[str, Any]:
    code = payload.get("code", "").strip()
    name = payload.get("name", "").strip()
    if not code:
        return {"status": "error", "message": "股票代码不能为空"}
    WatchlistRepository(settings.data.db_path).add(code, name)
    return {"status": "success", "message": f"成功将 {name or code} 加入自选股池"}


def remove_from_watchlist(settings: Settings, code: str) -> dict[str, Any]:
    WatchlistRepository(settings.data.db_path).remove(code)
    return {"status": "success", "message": f"已从自选股池移除 {code}"}


def signal_stats(settings: Settings) -> dict[str, Any]:
    from api.health_cache import health_cache
    from data.health import recommendation_state
    health = health_cache.get_background(settings)
    items = load_signals(settings, health=health)
    total_count = len(items)

    brackets = {
        "low": {"key": "low", "label": "黄金低价 (≤¥20)", "count": 0, "min_hand_cost": 999999.0, "top_stock": ""},
        "mid": {"key": "mid", "label": "稳健中价 (¥20-¥50)", "count": 0, "min_hand_cost": 999999.0, "top_stock": ""},
        "high": {"key": "high", "label": "成长中高 (¥50-¥100)", "count": 0, "min_hand_cost": 999999.0, "top_stock": ""},
        "top": {"key": "top", "label": "百元资产 (>¥100)", "count": 0, "min_hand_cost": 999999.0, "top_stock": ""},
    }

    for it in items:
        b = it.get("price_bracket", "low")
        if b in brackets:
            b_info = brackets[b]
            b_info["count"] += 1
            if it.get("hand_cost", 0) < b_info["min_hand_cost"]:
                b_info["min_hand_cost"] = it["hand_cost"]
            if not b_info["top_stock"]:
                b_info["top_stock"] = f"{it['name']} (¥{it['price']})"

    for k, v in brackets.items():
        if v["min_hand_cost"] == 999999.0:
            v["min_hand_cost"] = 0.0

    return {
        "total_count": total_count,
        "brackets": brackets,
        "readiness": dict(recommendation_state(settings, health),
                          audit_pending=health.get('health_cache_stale', False)),
    }
