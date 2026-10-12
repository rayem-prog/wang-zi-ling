"""所有交易入口共用的宏观买入闸门与资金计算。"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from config.settings import load_settings


def positive(value) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def current_policy(settings=None, as_of: str | None = None):
    settings = settings or load_settings()
    stance = 'neutral'
    path = Path(settings.data.db_path)
    if path.exists():
        conn = sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True)
        try:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='macro_history'").fetchone()
            if exists:
                row = conn.execute(
                    'SELECT stance FROM macro_history WHERE (? IS NULL OR date <= ?) ORDER BY date DESC LIMIT 1',
                    (as_of, as_of),
                ).fetchone()
                if row:
                    stance = str(row[0]).lower()
        finally:
            conn.close()
    cap = .3 if stance == 'defense' else (.8 if stance == 'attack' else .6)
    return SimpleNamespace(stance=stance, allow_new_buy=stance != 'defense',
                           max_total_position=min(settings.risk.max_total_position, cap))


def buying_allowed(policy) -> bool:
    return str(getattr(policy, 'stance', '')).lower() != 'defense' and getattr(policy, 'allow_new_buy', True)


def require_buy_allowed(settings=None) -> None:
    if not buying_allowed(current_policy(settings)):
        raise ValueError('宏观防守档禁止所有买入与加仓')


def buy_shares(target_amount, existing_value, cash, price, capacity=float('inf')) -> int:
    price = positive(price)
    if not price:
        return 0
    budget = min(max(positive(target_amount) - max(existing_value, 0), 0), max(cash, 0), max(capacity, 0))
    return int(budget // (price * 100)) * 100
