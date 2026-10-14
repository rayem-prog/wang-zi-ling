"""Collection outcome is distinct from daily, minute and training readiness."""
from data.health import health_conditions


def collection_components(updated, health):
    components=health_conditions(health)
    daily_errors=list(updated.get('failures', []))
    if not daily_errors:
        daily_errors=[dict(code=code,phase='daily',message=message,retryable=True)
                      for code,message in updated.get('failed_details',{}).items()]
    raw=updated.get('raw_update') or {}
    raw_errors=[dict(error,phase='raw_daily') for error in raw.get('failures',[])]
    if not raw_errors:
        raw_errors=[dict(code=code,phase='raw_daily',message=message,retryable=True)
                    for code,message in raw.get('failed_details',{}).items()]
    minutes=updated.get('minute_update') or {}
    minute_errors=[dict(code=code,phase='minute',message=message,retryable=True)
                   for code,message in minutes.get('failed_details',{}).items()]
    minute_errors.extend(dict(code=code,phase='minute',message='供应商返回空分钟数据',retryable=True)
                         for code in minutes.get('empty',[]))
    target=updated.get('target_trade_date')
    historical=[e for e in daily_errors if e.get('missing_dates') and target not in e['missing_dates']]
    current=[e for e in daily_errors if e not in historical]
    raw_historical=[e for e in raw_errors if e.get('missing_dates') and target not in e['missing_dates']]
    raw_current=[e for e in raw_errors if e not in raw_historical]
    components['daily'].update(failures=current,raw_failures=raw_current,
        collection_state='partial' if current or raw_current else 'complete',
        updated_codes=updated.get('updated',[]),skipped_codes=updated.get('skipped',[]))
    components['minute'].update(failures=minute_errors,
        collection_state='partial' if minute_errors else 'complete',updated_codes=minutes.get('updated',[]))
    components['training'].update(fetch_failures=historical,raw_fetch_failures=raw_historical,
        collection_state='partial' if historical or raw_historical else 'complete')
    errors=daily_errors+raw_errors+minute_errors
    if updated.get('spot_error'):
        errors.append(dict(phase='spot',message=updated['spot_error'],retryable=True))
    return components,errors
