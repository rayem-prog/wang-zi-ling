"""共享北京时间交易日和分钟缓存时效规则。"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from data.trading_calendar import closure_kind, previous_session, calendar_verified

SHANGHAI = ZoneInfo("Asia/Shanghai")


def exchange_now(now: dt.datetime | None = None) -> dt.datetime:
    now = now or dt.datetime.now(SHANGHAI)
    return now.replace(tzinfo=SHANGHAI) if now.tzinfo is None else now.astimezone(SHANGHAI)


def collection_minute_target(now=None):
    current=exchange_now(now)
    expected=minute_cache_state(None,None,now=current)['expected_at']
    if not expected:
        return None,None
    target=dt.datetime.fromisoformat(expected)
    # The live source has the same five-minute latency allowance as freshness checks.
    if target.date()==current.date() and target.time()<dt.time(15):
        target-=dt.timedelta(minutes=5)
    return target.date().isoformat(),target.strftime('%H:%M')


def minute_cache_state(date: str | None, time: str | None, *, now: dt.datetime | None = None,
                       tolerance_minutes: int = 5) -> dict:
    current = exchange_now(now)
    day = current.date()
    if not calendar_verified(day):
        return dict(stale=True,status="calendar_unverified",expected_at=None,reason="交易日历未核实")
    clock = current.time().replace(tzinfo=None)
    if closure_kind(day) or clock < dt.time(9,30):
        expected = dt.datetime.combine(previous_session(day - dt.timedelta(days=1) if not closure_kind(day) else day),dt.time(15),SHANGHAI)
    else:
        end = min(clock, dt.time(15))
        if dt.time(11,30) <= clock < dt.time(13):
            end = dt.time(11,30)
        expected = dt.datetime.combine(day,end,SHANGHAI)
    result = dict(stale=True,status="empty",expected_at=expected.isoformat(timespec="minutes"),reason="暂无分钟缓存")
    try:
        observed = dt.datetime.fromisoformat(f"{date}T{time}").replace(tzinfo=SHANGHAI)
    except (ValueError,TypeError):
        return result
    valid_time = (dt.time(9,30) <= observed.time().replace(tzinfo=None) <= dt.time(11,30) or
                  dt.time(13) <= observed.time().replace(tzinfo=None) <= dt.time(15))
    if not calendar_verified(observed.date()) or closure_kind(observed.date()) or not valid_time or observed > current:
        result.update(status="invalid",reason="分钟缓存包含休市、未核实或未来时间")
        return result
    stale = observed.date() != expected.date() or expected - observed > dt.timedelta(minutes=tolerance_minutes)
    result.update(stale=stale,status="stale" if stale else "current",
                  reason=f"分钟缓存停在 {date} {time}，预期 {expected.strftime('%Y-%m-%d %H:%M')}" if stale else "")
    return result


def quote_state(row, *, now: dt.datetime | None = None, tolerance_minutes: int = 5) -> dict:
    """Validate the vendor's timestamp per security; collection ts proves nothing."""
    current = exchange_now(now)
    result = dict(stale=True, reason='缺少可信报价时间', trade_date=row.get('trade_date'),
                  quote_time=row.get('quote_time'), price_source=row.get('source', 'spot'))
    try:
        value = str(row.get('quote_time') or '')
        if len(value) == 14 and value.isdigit():
            observed = dt.datetime.strptime(value, '%Y%m%d%H%M%S').replace(tzinfo=SHANGHAI)
        else:
            observed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
            observed = exchange_now(observed)
        if observed.date().isoformat() != str(row.get('trade_date')):
            result['reason'] = '报价时间与交易日不一致'; return result
        clock = current.time().replace(tzinfo=None)
        if not calendar_verified(current.date()) or not calendar_verified(observed.date()):
            result['reason'] = '交易日历未核实'; return result
        if closure_kind(current.date()) or clock < dt.time(9,15):
            day = previous_session(current.date() if closure_kind(current.date()) else current.date()-dt.timedelta(days=1))
            expected = dt.datetime.combine(day, dt.time(15), SHANGHAI)
        else:
            end = min(clock, dt.time(15))
            if dt.time(11,30) <= clock < dt.time(13): end = dt.time(11,30)
            expected = dt.datetime.combine(current.date(), end, SHANGHAI)
        market_clock = observed.time().replace(tzinfo=None)
        valid = (dt.time(9,15) <= market_clock <= dt.time(11,30) or dt.time(13) <= market_clock <= dt.time(15,5))
        stale = (observed > current or closure_kind(observed.date()) is not None or not valid
                 or observed.date() != expected.date()
                 or expected-observed > dt.timedelta(minutes=tolerance_minutes))
        result.update(stale=stale, reason='报价已过期、来自未来或非交易时段' if stale else '')
    except (ValueError, TypeError, OverflowError):
        pass
    return result
