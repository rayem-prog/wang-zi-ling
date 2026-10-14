"""系统状态与心跳监控 API。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from api.ws import get_market_trading_status
from config.settings import load_settings, settings_version
from api.health_cache import health_cache
from api.jobs import daily_jobs
from services.result_store import ResultStore, ResultStoreError
from services.result_contract import build_snapshot_response
from data.timeliness import exchange_now
from data import db
from data.health import assess_data_health, recommendation_state, health_conditions

router = APIRouter(prefix="/api/status", tags=["status"])


@router.post('/refresh')
def retry_market_collection():
    from api.jobs import JobConflict
    try:
        return daily_jobs.start(False,data_only=True,send_notification=False)
    except JobConflict as exc:
        raise HTTPException(status_code=409,detail=str(exc))


def evaluate_daemon_status(hb: dict | None, max_delay_seconds: int = 180) -> dict[str, Any]:
    if not hb:
        return {
            "online": False,
            "status": "offline",
            "title": "离线未运行",
            "detail": "暂无心跳记录",
            "pid": 0,
            "timestamp": "",
        }

    ts_str = hb.get("timestamp", "")
    status = hb.get("status", "unknown")
    pid = hb.get("pid", 0)
    msg = hb.get("message", "")

    try:
        ts = dt.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        now_utc = dt.datetime.now(dt.timezone.utc)
        elapsed = (now_utc - ts).total_seconds()
    except Exception:
        elapsed = 9999

    is_online = (elapsed <= max_delay_seconds) and (status in ("running", "started", "standby", "degraded"))
    return {
        "online": is_online,
        "status": status,
        "title": f"运行中 (PID: {pid})" if is_online else f"异常/离线 (PID: {pid})",
        "detail": f"最近心跳: {ts_str} ({int(elapsed)}秒前) | {msg}",
        "pid": pid,
        "timestamp": ts_str,
        "elapsed_seconds": int(elapsed),
        "config_version": hb.get('config_version', ''),
    }


@router.get("")
def get_system_status() -> dict[str, Any]:
    settings = load_settings()
    audit_state='unknown'
    conditions=None
    try:
        view = ResultStore(settings.paths.artifact_dir).read_view()
        current = exchange_now()
        response = build_snapshot_response(view, settings, now=current)
        audit = view.get('audit') or {}
        audit_state=response['audit_state']
        conditions=response['data_conditions']
        data_health = dict(audit.get('health') or {})
        try:
            deadline = dt.datetime.fromisoformat(audit.get('valid_until') or '')
            audit_fresh = deadline.tzinfo is not None and deadline > current
        except (ValueError, TypeError):
            audit_fresh = False
        market_ready = (audit.get('ready') is True and response['audit_state'] == 'passed' and audit_fresh
                        and response['job'].get('status') not in ('queued', 'running'))
        data_health.update(ready=market_ready,
                           health_cache_stale=not market_ready,
                           health_checked_at=audit.get('checked_at'))
        if not data_health.get('reason') and not data_health['ready']:
            data_health['reason'] = '后台核验尚未完成或认证已过期'
        recommendation = dict(ready=response['result_state'] == 'current',
            reason='；'.join(item['message'] for item in response['blocked_reasons']),
            result_state=response['result_state'], snapshot_id=response['snapshot_id'],
            trade_date=(view.get('snapshot') or {}).get('trade_date'),
            generated_at=response['generated_at'], action_permissions=response['action_permissions'])
        rec_count = response['counts']['snapshot_total']
        orders_count = len((view.get('snapshot') or {}).get('orders', [])) if recommendation['ready'] else 0
    except ResultStoreError as exc:
        data_health = dict(ready=False, health_cache_stale=True, reason=f'结果库暂时不可读: {exc}')
        recommendation = dict(ready=False, reason=data_health['reason'], result_state='unknown')
        rec_count = orders_count = 0
    try:
        hb = db.get_latest_heartbeat(settings.data.db_path)
    except Exception:
        db.init_db(settings.data.db_path)
        hb = db.get_latest_heartbeat(settings.data.db_path)
    daemon_info = evaluate_daemon_status(hb)

    # 宏观信息
    try:
        macro_df = db.load_macro_history(settings.data.db_path, limit=1)
    except Exception:
        macro_df = db.pd.DataFrame() if hasattr(db, "pd") else None
    if not macro_df.empty:
        latest_macro = macro_df.iloc[0].to_dict()
        stance = str(latest_macro.get("stance", "NEUTRAL")).upper()
        cap = 0.8 if stance == "ATTACK" else (0.3 if stance == "DEFENSE" else 0.6)
        allow_buy = stance != "DEFENSE"
        macro_info = {
            "score": float(latest_macro.get("score", 0.0)),
            "stance": stance,
            "cap": cap,
            "allow_new_buy": allow_buy,
            "date": str(latest_macro.get("date", "")),
        }
    else:
        macro_info = {
            "score": 0.0,
            "stance": "NEUTRAL",
            "cap": 0.6,
            "allow_new_buy": True,
            "date": "",
        }

    return {
        "server_time": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "config_version": settings_version(settings),
        "market": get_market_trading_status(),
        "data_health": data_health,
        "data_conditions": conditions or health_conditions(data_health,audit_state=audit_state,fresh=False),
        "data_update": daily_jobs.latest_summary(),
        "recommendation": recommendation,
        "daemon": daemon_info,
        "macro": macro_info,
        "counts": {
            "recommendations": rec_count,
            "orders": orders_count,
        },
    }
