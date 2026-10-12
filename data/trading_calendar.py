"""已核对的 A 股交易所休市日。未知年份按工作日推断并由行情门禁保守阻断。"""

from __future__ import annotations

import datetime as dt

# 上交所官方年度休市日，覆盖当前数据库历史范围及 2026 年。
# 年度公告入口：
# 2021 https://www.sse.com.cn/disclosure/announcement/general/c/c_20201224_5286949.shtml
# 2022 https://www.sse.com.cn/disclosure/announcement/general/c/c_20211220_5662606.shtml
# 2023 https://www.sse.com.cn/disclosure/announcement/general/c/c_20221227_5714458.shtml
# 2024 https://www.sse.com.cn/disclosure/announcement/general/c/c_20231226_5733939.shtml
# 2025 https://big5.sse.com.cn/site/cht/www.sse.com.cn/disclosure/announcement/general/c/c_20241223_10767108.shtml
# 2026 https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml
# 深交所 2026-09-17 公告亦确认中秋节 9/25 至 9/27 休市。
_CLOSURES = (
    ("2021-01-01", "2021-01-03"), ("2021-02-11", "2021-02-17"),
    ("2021-04-03", "2021-04-05"), ("2021-05-01", "2021-05-05"),
    ("2021-06-12", "2021-06-14"), ("2021-09-19", "2021-09-21"),
    ("2021-10-01", "2021-10-07"),
    ("2022-01-01", "2022-01-03"), ("2022-01-31", "2022-02-06"),
    ("2022-04-03", "2022-04-05"), ("2022-04-30", "2022-05-04"),
    ("2022-06-03", "2022-06-05"), ("2022-09-10", "2022-09-12"),
    ("2022-10-01", "2022-10-07"),
    ("2023-01-01", "2023-01-02"), ("2023-01-21", "2023-01-27"),
    ("2023-04-05", "2023-04-05"), ("2023-04-29", "2023-05-03"),
    ("2023-06-22", "2023-06-24"), ("2023-09-29", "2023-10-06"),
    ("2024-01-01", "2024-01-01"), ("2024-02-09", "2024-02-17"),
    ("2024-04-04", "2024-04-06"), ("2024-05-01", "2024-05-05"),
    ("2024-06-10", "2024-06-10"), ("2024-09-15", "2024-09-17"),
    ("2024-10-01", "2024-10-07"),
    ("2025-01-01", "2025-01-01"), ("2025-01-28", "2025-02-04"),
    ("2025-04-04", "2025-04-06"), ("2025-05-01", "2025-05-05"),
    ("2025-05-31", "2025-06-02"), ("2025-10-01", "2025-10-08"),
    ("2026-01-01", "2026-01-03"),
    ("2026-02-15", "2026-02-23"),
    ("2026-04-04", "2026-04-06"),
    ("2026-05-01", "2026-05-05"),
    ("2026-06-19", "2026-06-21"),
    ("2026-09-25", "2026-09-27"),
    ("2026-10-01", "2026-10-07"),
)


def closure_kind(day: dt.date) -> str | None:
    if day.weekday() >= 5:
        return "weekend"
    if any(start <= day.isoformat() <= end for start, end in _CLOSURES):
        return "exchange_holiday"
    return None


def contains_expected_session(start: str, end: str) -> bool:
    """Whether an interval contains any weekday not on the verified holiday list."""
    day = dt.date.fromisoformat(start)
    last = dt.date.fromisoformat(end)
    while day <= last:
        if closure_kind(day) is None:
            return True
        day += dt.timedelta(days=1)
    return False


def previous_session(day: dt.date) -> dt.date:
    while closure_kind(day):
        day -= dt.timedelta(days=1)
    return day


def calendar_verified(day: dt.date) -> bool:
    """只有已录入官方年度休市表的年份可用于可信训练。"""
    return 2021 <= day.year <= 2026


def expected_sessions(start: str, end: str) -> list[str]:
    day, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    result = []
    while day <= last:
        if calendar_verified(day) and closure_kind(day) is None:
            result.append(day.isoformat())
        day += dt.timedelta(days=1)
    return result
