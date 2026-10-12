"""Worker-side version freezing, audit certificates and legacy migration."""
from __future__ import annotations

from dataclasses import replace
import datetime as dt
from hashlib import sha256
import json
import math
from pathlib import Path

from config.settings import settings_version
from data.timeliness import exchange_now, SHANGHAI
from data.trading_calendar import calendar_verified, closure_kind
from services.recommendations import format_rows, read_legacy_rows
from services.result_store import ResultStore, _validate_payload

STRATEGY_VERSION = 'daily-decision-v1'


def data_version(settings, signature=None):
    if signature is None:
        from api.health_cache import market_signature
        signature = market_signature(settings)
    return sha256(repr(signature).encode()).hexdigest()


def audit_deadline(settings, trade_date, now=None):
    current = exchange_now(now)
    try:
        day = dt.date.fromisoformat(trade_date)
    except (ValueError, TypeError):
        return None
    if not calendar_verified(day) or not calendar_verified(current.date()):
        return None
    if settings.engine.use_intraday_factors:
        return (current + dt.timedelta(minutes=5)).isoformat()
    day += dt.timedelta(days=1)
    while calendar_verified(day) and closure_kind(day):
        day += dt.timedelta(days=1)
    if not calendar_verified(day):
        return None
    return dt.datetime.combine(day, dt.time(16, 10), SHANGHAI).isoformat()


def record_health_projection(settings, health, status, signature=None, *, now=None, ttl_seconds=120):
    """Persist only fresh audit evidence; errors retain their source/time context."""
    store = ResultStore(settings.paths.artifact_dir)
    previous = store.read_view().get('audit') or {}
    current = exchange_now(now)
    ready = status == 'passed' and health.get('ready') is True
    checked_value = health.get('health_checked_at')
    checked = exchange_now(dt.datetime.fromisoformat(checked_value)).isoformat() if checked_value else current.isoformat()
    deadline = audit_deadline(settings, health.get('trade_date'), current) if ready else None
    if deadline:
        deadline = min(dt.datetime.fromisoformat(deadline), current + dt.timedelta(seconds=min(ttl_seconds, 120))).isoformat()
    error = None
    if status == 'failed':
        error = dict(code=health.get('error_code', 'audit_failed'),
                     message=health.get('reason') or '健康审计未完成', retryable=True)
    allow_new_buy, policy_error = None, None
    if ready:
        try:
            from strategy.risk import current_policy
            policy = current_policy(settings)
            allow_new_buy = policy.allow_new_buy if isinstance(policy.allow_new_buy, bool) else None
        except Exception as exc:
            policy_error = dict(code='risk_projection_failed', message=f'风控状态未确认: {exc}', retryable=True)
    projection = dict(state=status, ready=ready and deadline is not None,
                      data_version=data_version(settings, signature) if signature is not None or ready else previous.get('data_version'),
                      parameters_version=settings_version(settings),
                      checked_at=checked if status in ('passed', 'failed') else previous.get('checked_at'),
                      valid_until=deadline, error=error or policy_error, allow_new_buy=allow_new_buy,
                      source=health.get('source', previous.get('source')),
                      trade_date=health.get('trade_date', previous.get('trade_date')),
                      health_as_of=health.get('health_as_of', previous.get('health_as_of')),
                      health=dict(health))
    store.record_audit(projection)
    return projection


def decision_payload(settings, decision, provenance, frozen, *, now=None):
    """Keep the ledger's actual frozen identity, even after settings edits."""
    frozen_settings = settings
    for section in ('risk', 'account', 'engine'):
        values = decision.parameters.get(section)
        if values is not None:
            frozen_settings = replace(frozen_settings, **{section: type(getattr(settings, section))(**values)})
    parameter_identity = provenance.get('parameters_version')
    if not parameter_identity:
        parameter_identity = 'frozen:' + sha256(json.dumps(decision.parameters, sort_keys=True).encode()).hexdigest()
    rows = format_rows(frozen_settings, decision.plan)
    for row in rows:
        row.update({key: value for key, value in provenance.items() if key not in ('parameters_version', 'data_version')})
    count = len(decision.scores)
    if len(rows) > count:
        raise ValueError('最终推荐数量超过完整评分数量')
    funnel = [dict(name='rank_and_position_limit', input=count, output=len(rows),
                   excluded_by_reason={'outside_final_ranked_plan': count-len(rows)})]
    current = exchange_now(now)
    data_as_of = dt.datetime.combine(dt.date.fromisoformat(decision.date), dt.time(15), SHANGHAI).isoformat()
    result = dict(rows=rows, orders=frozen.get('orders', []), trade_date=decision.date,
                  data_as_of=data_as_of, generated_at=provenance['generated_at'],
                  source=provenance.get('source'), coverage=provenance.get('coverage'),
                  versions=dict(data=provenance['data_version'],
                                strategy=STRATEGY_VERSION+':'+decision.input_digest+':'+str(decision.model_audit.get('model_digest', 'neutral')),
                                parameters=parameter_identity),
                  valid_until=audit_deadline(settings, decision.date, current),
                  allow_new_buy=bool(decision.macro.allow_new_buy), selection=dict(funnel=funnel),
                  parameters=decision.parameters, scores_count=count, model_audit=decision.model_audit,
                  rule_audit=decision.rule_audit, risk=dict(stance=decision.macro.stance,
                                                        allow_new_buy=decision.macro.allow_new_buy))
    _validate_payload(result)
    return result


def bootstrap_results(settings):
    """Recover durable jobs and import a complete legacy artifact once at startup."""
    from api.jobs import daily_jobs, GenerationLease
    daily_jobs.recover(settings)
    store = ResultStore(settings.paths.artifact_dir)
    if store.read_view()['snapshot'] is not None:
        return
    directory = Path(settings.paths.artifact_dir)
    csv, metadata = directory/'recommendations.csv', directory/'latest_recommendations.meta.json'
    if not csv.exists() and not metadata.exists():
        return
    lease = GenerationLease(directory)
    if not lease.acquire():
        return
    try:
        if store.read_view()['snapshot'] is not None:
            return
        meta = json.loads(metadata.read_text(encoding='utf-8'))
        required = ('trade_date', 'generated_at', 'source', 'coverage', 'history_coverage', 'updated_at', 'price_source')
        if any(key not in meta for key in required):
            raise ValueError('旧推荐元数据缺失，不能确认来源和时点')
        day = dt.date.fromisoformat(meta['trade_date'])
        generated = dt.datetime.fromisoformat(meta['generated_at'])
        if generated.tzinfo is None or generated.utcoffset() is None:
            raise ValueError('旧推荐生成时间缺少时区')
        if any(term in str(meta['source']).lower() for term in ('simulat', 'demo', 'mock', 'synthetic')):
            raise ValueError('模拟或演示来源不能导入')
        for field in ('coverage', 'history_coverage'):
            if not isinstance(meta[field], (float, int)) or not math.isfinite(meta[field]) or not 0<=meta[field]<=1:
                raise ValueError('旧推荐覆盖率无效')
        rows = read_legacy_rows(settings, strict=True)
        for row in rows:
            for field in ('trade_date', 'generated_at'):
                if row.get(field) and row[field] != meta[field]:
                    raise ValueError('旧推荐行时点与元数据不一致')
            row.update(meta)
        digest = sha256(csv.read_bytes()+metadata.read_bytes()).hexdigest()
        payload = dict(rows=rows, orders=[], trade_date=day.isoformat(), generated_at=generated.isoformat(),
                       data_as_of=dt.datetime.combine(day, dt.time(15), SHANGHAI).isoformat(),
                       versions=dict(data='legacy:'+digest, strategy='unknown:legacy', parameters='unknown:legacy'),
                       legacy_import=True, source=meta['source'], valid_until=None, allow_new_buy=None,
                       selection={'funnel': []}, legacy_sha256=digest)
        store.publish(store.stage(payload))
    except Exception as exc:
        store.record_migration_failure(dict(code='legacy_import_failed', message=f'旧推荐导入失败: {exc}', retryable=False))
        record_health_projection(settings, dict(ready=False, reason=f'旧推荐导入失败: {exc}', error_code='legacy_import_failed'), 'failed')
    finally:
        lease.release()
