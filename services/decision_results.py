"""Current decision eligibility and immutable inputs for operation consumers.

Page reads use result_contract. This module is only for consumers that already
performed a current market audit; it never promotes display history to tradable.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from hashlib import sha256
import sqlite3

from config.settings import settings_version
from data.timeliness import exchange_now
from services.result_store import ResultStore, ResultStoreError


class DecisionUnavailable(ValueError):
    pass


def current_data_version(settings):
    from api.health_cache import market_signature
    return sha256(repr(market_signature(settings)).encode()).hexdigest()


def snapshot_operation_state(settings, health, *, expected_snapshot_id=None, view=None):
    """Return None only when the installation has no published snapshot yet."""
    view = view if view is not None else ResultStore(settings.paths.artifact_dir).read_view()
    snapshot = view['snapshot']
    if snapshot is None:
        if view.get('migration_error'):
            return dict(ready=False, trade_date=None, reason=view['migration_error']['message'] + '；请重新生成结果')
        if expected_snapshot_id:
            raise DecisionUnavailable('该结果版本已不可用，请重新读取榜单')
        return None
    result = dict(ready=False,reason='',trade_date=snapshot['trade_date'],
                  generated_at=snapshot['generated_at'],snapshot_id=snapshot['snapshot_id'],
                  source=snapshot.get('source','published_snapshot'),coverage=snapshot.get('coverage',0))
    if view['revocation']:
        result['reason'] = '该结果已撤销：' + view['revocation']['message']
    elif expected_snapshot_id and expected_snapshot_id != snapshot['snapshot_id']:
        result['reason'] = '结果版本已更新，请重新读取榜单后再操作'
    elif (view.get('job') or {}).get('status') in ('queued', 'running'):
        result['reason'] = '后台正在更新结果，请等待更新完成后再操作'
    elif not health.get('ready'):
        result['reason'] = health.get('reason') or '行情数据未通过完整性检查'
    elif snapshot.get('legacy_import'):
        result['reason'] = '历史导入结果仅供回看，请重新生成当前结果'
    elif snapshot['trade_date'] != health.get('trade_date'):
        result['reason'] = '历史结果与当前行情日期不一致，请重新生成'
    elif snapshot['versions']['parameters'] != settings_version(settings):
        result['reason'] = '结果参数已变化，请重新生成'
    else:
        audit=view.get('audit') or {}
        now=exchange_now()
        def fresh(value):
            try:
                stamp=datetime.fromisoformat(value)
                return stamp.tzinfo is not None and stamp>now
            except (ValueError,TypeError):
                return False
        if (audit.get('state') != 'passed' or not audit.get('ready')
            or audit.get('data_version') != snapshot['versions']['data']
            or audit.get('parameters_version') != snapshot['versions']['parameters']
            or not fresh(audit.get('valid_until')) or not fresh(snapshot.get('valid_until'))):
            result['reason']='结果核验未完成或已过期，请重新确认后再操作'
        elif current_data_version(settings) != snapshot['versions']['data']:
            result['reason']='结果对应的行情版本已变化，请重新生成'
        else:
            result['ready']=True
    return result


@contextmanager
def decision_context(settings, health, *, expected_snapshot_id=None, for_new_buy=False):
    """Serialize publication/revocation against the short operation transaction.

Heavy market audits happen before entry. Holding the result writer lock through
execution prevents a version being revoked between its final check and a buy.
"""
    store=ResultStore(settings.paths.artifact_dir)
    connection=None
    try:
        if store.path.exists():
            connection=sqlite3.connect(f'{store.path.as_uri()}?mode=rw',uri=True,timeout=.1)
            connection.execute('BEGIN IMMEDIATE')
        view=store.read_view()
        state=snapshot_operation_state(settings,health,expected_snapshot_id=expected_snapshot_id,view=view)
        if state is None:
            from data.health import recommendation_state
            state=recommendation_state(settings,health)
        if not state['ready']:
            raise DecisionUnavailable(state['reason'])
        if for_new_buy:
            from strategy import risk
            if not risk.buying_allowed(risk.current_policy(settings)):
                raise DecisionUnavailable('宏观防守档禁止所有买入与加仓')
        yield view['snapshot']
    except (sqlite3.Error,ResultStoreError) as exc:
        raise DecisionUnavailable('结果状态暂时不可确认，请稍后重试') from exc
    finally:
        if connection is not None:
            connection.rollback()
            connection.close()


def operation_frame(settings, state, kind='rows'):
    """Read frozen inputs for a checked version; legacy files are migration-only."""
    import pandas as pd
    if not state.get('ready'):
        return pd.DataFrame()
    view=ResultStore(settings.paths.artifact_dir).read_view()
    if view['snapshot'] is not None:
        if view['revocation'] or state.get('snapshot_id') != view['snapshot']['snapshot_id']:
            return pd.DataFrame()
        return pd.DataFrame(view['snapshot'].get(kind, []))
    from pathlib import Path
    filename='latest_orders.csv' if kind=='orders' else 'recommendations.csv'
    path=Path(settings.paths.artifact_dir)/filename
    return pd.read_csv(path,dtype={'code':str}) if path.exists() else pd.DataFrame()


def require_candidate(snapshot, code):
    if snapshot is not None:
        row=next((row for row in snapshot['rows'] if row['code']==str(code).zfill(6)),None)
        if row is None or row.get('buyable'):
            raise DecisionUnavailable('该标的不属于此版本可买入候选，请重新选择')


def require_snapshot_reference(settings, snapshot_id):
    """New buys must bind their displayed version once snapshot storage is active."""
    try:
        view=ResultStore(settings.paths.artifact_dir).read_view()
    except ResultStoreError as exc:
        raise DecisionUnavailable('结果状态暂时不可确认，请稍后重试') from exc
    if view['snapshot'] is not None and not snapshot_id:
        raise DecisionUnavailable('请提供当前结果版本 snapshot_id 后再买入')
