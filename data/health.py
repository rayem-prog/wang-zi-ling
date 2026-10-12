"""本地行情与推荐的新鲜度门禁。无法确认交易日时保守阻断推荐。"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

import pandas as pd

from config.settings import Settings
from data.security_master import inactive_status, security_metadata
from data.trading_calendar import closure_kind, previous_session, calendar_verified
from data.timeliness import minute_cache_state, exchange_now


def _expected_session(now: dt.datetime) -> dt.date:
    now = exchange_now(now)
    day = now.date()
    if closure_kind(day) is None and now.time().replace(tzinfo=None) < dt.time(16, 10):
        day -= dt.timedelta(days=1)
    return previous_session(day)


def assess_data_health(
    settings: Settings,
    now: dt.datetime | None = None,
    expected_codes: list[str] | None = None,
    min_coverage: float = 0.98,
) -> dict:
    """计算最新指数交易日的股票覆盖；工作日假期无法从离线日历确认时保持阻断。"""
    now = exchange_now(now)
    expected_day = _expected_session(now).isoformat()
    result = {
        "ready": False, "trade_date": None, "expected_trade_date": expected_day,
        "calendar_status": ({"weekend": "non_trading_day", "exchange_holiday": "exchange_holiday"}
                            .get(closure_kind(now.date()), "trading_day_or_unverified_holiday")),
        "coverage": 0.0, "covered_codes": 0, "expected_codes": 0, "pool_codes": 0,
        "eligible_codes": [], "excluded_codes": [],
        "history_coverage": 0.0, "history_covered_codes": 0,
        "benchmark_history_start": None, "benchmark_history_rows": 0,
        "missing_codes": [], "source": "unknown", "updated_at": "", "reason": "",
        "trusted_daily_rows": 0, "quarantined_daily_rows": 0,
        "training_ready": False, "internal_gaps": [],
        "minute_rows": 0, "minute_latest_date": None,
        "minute_source": "unknown", "minute_updated_at": "",
        "minute_status": "empty", "minute_source_errors": {},
        "minute_coverage": 0.0, "minute_covered_codes": 0,
        "minute_expected_codes": 0, "minute_missing_codes": [],
        "classifications": {"source_errors": {}, "possible_suspension_or_gap": [],
                            "historical_short": [], "insufficient_feature_history": [],
                            "suspected_truncation": [], "confirmed_suspensions": [],
                            "delisted": []},
    }
    db_path = Path(settings.data.db_path)
    if not db_path.exists():
        result["reason"] = "行情数据库不存在"
        return result
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            columns = {r[1] for r in conn.execute("PRAGMA table_info(bars)")}
            real_bars = "coalesce(source, '') NOT IN ('simulation','demo','synthetic')"
            real_minutes = "coalesce(source, '') NOT IN ('simulation','demo','synthetic','tencent_price_line')"
            latest = conn.execute(f"SELECT MAX(date) FROM bars WHERE code=? AND {real_bars}",
                                  (settings.data.index_symbol,)).fetchone()[0]
            result["trade_date"] = latest
            result["minute_rows"] = conn.execute(f"SELECT COUNT(*) FROM minute_bars WHERE {real_minutes}").fetchone()[0]
            minute_meta = conn.execute(
                f"SELECT date,source,fetched_at FROM minute_bars WHERE {real_minutes} ORDER BY date DESC,time DESC LIMIT 1"
            ).fetchone()
            if minute_meta:
                result["minute_latest_date"] = minute_meta[0]
                result["minute_source"] = minute_meta[1] or "unknown"
                result["minute_updated_at"] = minute_meta[2] or ""
                result["minute_status"] = ("current" if latest and minute_meta[0] >= latest else "stale")
            if expected_codes is None:
                spot_codes = [r[0] for r in conn.execute("SELECT code FROM spot")]
                expected_codes = spot_codes or [r[0] for r in conn.execute(
                    f"SELECT DISTINCT code FROM bars WHERE code<>? AND {real_bars}", (settings.data.index_symbol,)
                )]
            pool_codes = sorted({str(code).zfill(6) for code in expected_codes
                                 if str(code) != settings.data.index_symbol})
            inactive = [status for code in pool_codes
                        if (status := inactive_status(code, expected_day)) is not None]
            result["pool_codes"] = len(pool_codes)
            result["excluded_codes"] = inactive
            result["classifications"]["confirmed_suspensions"] = [
                item for item in inactive if item["status"] == "suspended"
            ]
            result["classifications"]["delisted"] = [
                item for item in inactive if item["status"] == "delisted"
            ]
            codes = [code for code in pool_codes
                     if inactive_status(code, expected_day) is None]
            result["eligible_codes"] = codes
            result["expected_codes"] = len(codes)
            result["minute_expected_codes"] = len(codes)
            result["minute_missing_codes"] = list(codes)
            if result["minute_latest_date"]:
                minute_codes = {row[0] for row in conn.execute(
                    f"SELECT DISTINCT code FROM minute_bars WHERE date=? AND {real_minutes}",
                    (result["minute_latest_date"],),
                )}
                covered_minutes = sorted(set(codes) & minute_codes)
                result["minute_covered_codes"] = len(covered_minutes)
                result["minute_missing_codes"] = sorted(set(codes) - minute_codes)
                result["minute_coverage"] = round(len(covered_minutes) / len(codes), 4) if codes else 0.0
            if latest:
                have = {r[0] for r in conn.execute(f"SELECT code FROM bars WHERE date=? AND {real_bars}", (latest,))}
                missing = sorted(set(codes) - have)
                result["missing_codes"] = missing
                result["covered_codes"] = len(codes) - len(missing)
                result["coverage"] = round(result["covered_codes"] / len(codes), 4) if codes else 0.0
                if "source" in columns:
                    meta = conn.execute(
                        "SELECT source,fetched_at FROM bars WHERE code=? AND date=?",
                        (settings.data.index_symbol, latest),
                    ).fetchone()
                    if meta:
                        result["source"] = meta[0] or "unknown"
                        result["updated_at"] = meta[1] or ""
            history = {row[0]: (row[1], row[2]) for row in conn.execute(
                f"SELECT code,MIN(date),COUNT(*) FROM bars WHERE {real_bars} GROUP BY code"
            )}
            base_history_start = dt.date.fromisoformat(settings.data.start_date)
            bench_history = history.get(settings.data.index_symbol, (None, 0))
            result["benchmark_history_start"], result["benchmark_history_rows"] = bench_history
            def history_cutoff_for(code: str) -> str:
                metadata = security_metadata(code) or {}
                listed = dt.date.fromisoformat(metadata["listed_on"]) if metadata.get("listed_on") else base_history_start
                return (max(base_history_start, listed) + dt.timedelta(days=30)).isoformat()

            result["classifications"]["historical_short"] = [
                code for code in codes if history.get(code)
                and history[code][0] > history_cutoff_for(code)
            ]
            result["classifications"]["insufficient_feature_history"] = [
                code for code in codes if history.get(code, (None, 0))[1] < 61
            ]
            result["classifications"]["suspected_truncation"] = [
                code for code in codes if history.get(code, (None, 0))[1] == 640
                and history[code][0] > history_cutoff_for(code)
            ]
            history_bad = set(result["classifications"]["historical_short"])
            history_bad.update(result["classifications"]["insufficient_feature_history"])
            history_bad.update(result["classifications"]["suspected_truncation"])
            result["history_covered_codes"] = len(codes) - len(history_bad)
            result["history_coverage"] = round((len(codes) - len(history_bad)) / len(codes), 4) if codes else 0.0
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if "data_fetch_log" in tables:
                errors = conn.execute(
                    "SELECT log.code,log.error FROM data_fetch_log log JOIN "
                    "(SELECT code,MAX(id) AS last_id FROM data_fetch_log "
                    "WHERE kind='daily' GROUP BY code) latest "
                    "ON log.code=latest.code AND log.id=latest.last_id WHERE log.status='error'"
                ).fetchall()
                result["classifications"]["source_errors"] = {
                    code: error for code, error in errors if code in codes
                }
                minute_errors = conn.execute(
                    "SELECT log.code,log.error FROM data_fetch_log log JOIN "
                    "(SELECT code,MAX(id) AS last_id FROM data_fetch_log "
                    "WHERE kind='minute' GROUP BY code) latest "
                    "ON log.code=latest.code AND log.id=latest.last_id WHERE log.status='error'"
                ).fetchall()
                result["minute_source_errors"] = dict(minute_errors)
            result["classifications"]["possible_suspension_or_gap"] = [
                code for code in result["missing_codes"]
                if code not in result["classifications"]["source_errors"]
            ]
    except (sqlite3.Error, OSError) as exc:
        result["reason"] = f"行情数据库读取失败: {exc}"
        return result
    trusted_reasons = []
    if not calendar_verified(now.date()):
        trusted_reasons.append("交易日历年份尚未核实")
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(bars)")}
            if "verification_status" not in columns:
                trusted_reasons.append("数据库尚未建立可信数据基线")
            else:
                result["trusted_daily_rows"] = conn.execute("SELECT COUNT(*) FROM trusted_bars").fetchone()[0]
                result["quarantined_daily_rows"] = conn.execute("SELECT COUNT(*) FROM bars WHERE verification_status<>'verified'").fetchone()[0]
                from data.gaps import classify_internal_gaps
                result["internal_gaps"] = classify_internal_gaps(conn)
                from data.baseline import load_training_bars
                trusted_codes = []
                trusted_history_codes = []
                result['classifications']['insufficient_recent_history'] = []
                for code in result["eligible_codes"]:
                    frame = load_training_bars(str(db_path), code, adjustment=settings.data.adjust,
                                               gap_policy='segments')
                    recent_rows = (len(frame[frame.history_segment == frame.history_segment.iloc[-1]])
                                   if not frame.empty else 0)
                    if recent_rows < 61:
                        result['classifications']['insufficient_recent_history'].append(code)
                    if recent_rows >= 61 and str(frame["date"].max()) >= expected_day:
                        trusted_codes.append(code)
                    if len(frame) >= 61 and str(frame["date"].min()) <= history_cutoff_for(code):
                        trusted_history_codes.append(code)
                result["trusted_codes"] = trusted_codes
                result["trusted_history_codes"] = trusted_history_codes
                count = result["expected_codes"]
                result["trusted_coverage"] = len(trusted_codes) / count if count else 0.0
                result["trusted_history_coverage"] = len(trusted_history_codes) / count if count else 0.0
                if result["trusted_coverage"] < min_coverage or result["trusted_history_coverage"] < min_coverage:
                    trusted_reasons.append("股票可信历史或最新连续61条日线覆盖不足98%，不能生成推荐")
                result['history_gap_policy'] = 'segments; no filled bars, no factors across gaps'
                trusted_bench = load_training_bars(str(db_path), settings.data.index_symbol, adjustment="none")
                result["trusted_trade_date"] = str(trusted_bench["date"].max()) if not trusted_bench.empty else None
                if len(trusted_bench) < 61 or not result["trusted_trade_date"] or result["trusted_trade_date"] < expected_day:
                    trusted_reasons.append("基准指数尚无覆盖最新完整交易日的可信完整快照")
                minute = conn.execute("SELECT date,time FROM minute_bars ORDER BY date DESC,time DESC LIMIT 1").fetchone()
                minute_state = minute_cache_state(minute[0] if minute else None, minute[1] if minute else None, now=now)
                result["minute_status"] = minute_state["status"]
                result["minute_stale"] = minute_state["stale"]
                result["minute_expected_at"] = minute_state["expected_at"]
                result["minute_reason"] = minute_state["reason"]
                result["minute_fresh_codes"] = []
                for code, date, time in conn.execute("SELECT code,MAX(date||' '||time),'' FROM minute_bars GROUP BY code"):
                    date, time = date.split(' ',1)
                    if code in result["eligible_codes"] and not minute_cache_state(date,time,now=now)["stale"]:
                        result["minute_fresh_codes"].append(code)
                result["minute_fresh_coverage"] = len(result["minute_fresh_codes"]) / count if count else 0.0
                if result["minute_fresh_coverage"] < min_coverage:
                    result["minute_status"] = "stale" if result["minute_rows"] else "empty"
                    result["minute_stale"] = True
                from data.baseline import load_training_minute_bars
                result["minute_trusted_fresh_codes"] = [code for code in result["eligible_codes"]
                    if not load_training_minute_bars(str(db_path), code, live=True, now=now).empty]
                result["minute_trusted_fresh_coverage"] = len(result["minute_trusted_fresh_codes"]) / count if count else 0.0
                if settings.engine.use_intraday_factors and result["minute_trusted_fresh_coverage"] < min_coverage:
                    trusted_reasons.append("分钟因子开启，但可信分钟缓存过期、复权版本混合或缺少验证证据")
    except sqlite3.Error as exc:
        trusted_reasons.append(f"可信数据检查失败: {exc}")
    reasons = trusted_reasons
    if not result["trade_date"]:
        reasons.append("缺少基准指数日线")
    elif result["trade_date"] < expected_day:
        reasons.append(f"基准指数停在 {result['trade_date']}，预期至少 {expected_day}；可能休市或采集失败")
    if not result["expected_codes"]:
        reasons.append("没有可核对的股票池")
    elif result["coverage"] < min_coverage:
        reasons.append(
            f"股票日线覆盖 {result['covered_codes']}/{result['expected_codes']}，"
            f"低于 {min_coverage:.0%} 门槛；缺口可能包含停牌或采集失败"
        )
    if result["expected_codes"] and result["history_coverage"] < min_coverage:
        reasons.append(
            f"可用历史覆盖 {result['history_covered_codes']}/{result['expected_codes']}，"
            f"低于 {min_coverage:.0%} 门槛；"
            f"{len(result['classifications']['historical_short'])} 只历史起点晚于配置，尚未核实上市日或源截断；其中 "
            f"{len(result['classifications']['suspected_truncation'])} 只恰好 640 条疑似截断，"
            f"{len(result['classifications']['insufficient_feature_history'])} 只不足 61 条"
        )
    history_cutoff = (dt.date.fromisoformat(settings.data.start_date)
                      + dt.timedelta(days=30)).isoformat()
    if (result["benchmark_history_rows"] < 61 or
            (result["benchmark_history_start"] and result["benchmark_history_start"] > history_cutoff)):
        reasons.append("基准指数历史不足或历史起点晚于配置，需要补采后再生成推荐")
    result["reason"] = "；".join(reasons)
    result["ready"] = not reasons
    result["training_ready"] = result["ready"]
    return result


def recommendation_state(settings: Settings, health: dict) -> dict:
    """旧推荐无元数据，或元数据日期与行情不符时不得作为有效信号。"""
    # Published snapshots own decision validity; display uses a separate contract.
    from services.decision_results import snapshot_operation_state
    from services.result_store import ResultStoreError
    try:
        published = snapshot_operation_state(settings, health)
    except (ResultStoreError, ValueError) as exc:
        return {"ready": False, "reason": f"结果状态不可确认: {exc}", "trade_date": None}
    if published is not None:
        return published
    result = {"ready": False, "reason": "", "trade_date": None,
              "source": "unknown", "generated_at": "", "coverage": 0.0}
    if not health.get("ready"):
        result["reason"] = health.get("reason") or "行情数据未通过完整性检查"
        return result
    artifact_dir = Path(settings.paths.artifact_dir)
    meta_path = artifact_dir / "latest_recommendations.meta.json"
    if not (artifact_dir / "recommendations.csv").exists() or not meta_path.exists():
        result["reason"] = "缺少带来源和日期的有效推荐文件"
        return result
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        result["reason"] = f"推荐元数据无法读取: {exc}"
        return result
    result.update({key: meta.get(key, result[key]) for key in
                   ("trade_date", "source", "generated_at", "coverage")})
    if meta.get("trade_date") != health.get("trade_date"):
        result["reason"] = "推荐对应的交易日与最新行情不一致"
        return result
    try:
        if pd.read_csv(artifact_dir / "recommendations.csv").empty:
            result["reason"] = "当前交易日没有可用推荐"
            return result
    except (OSError, ValueError, pd.errors.EmptyDataError) as exc:
        result["reason"] = f"推荐文件无法读取: {exc}"
        return result
    result["ready"] = True
    return result
