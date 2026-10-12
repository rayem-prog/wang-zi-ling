"""Latest complete session + observed history + the user's current portfolio."""
import datetime as dt
from dataclasses import replace
from data.health import _expected_session
from data.timeliness import exchange_now
from data.trading_calendar import calendar_verified
import pandas as pd
from api.health_cache import health_cache
from config.settings import load_settings
from data import db
from features.alpha import compute_features
from strategy import holdings
from strategy.decision import decide, frame_records
from strategy.evaluation import PredictionLedger
from services.plan_evidence import observed_evidence


def build_personal_plan(progress=lambda *args: None):
    settings = load_settings()
    progress('核验最新完整交易日与历史覆盖')
    health = health_cache.get(settings, wait=True)
    limited = not health.get('ready')
    eligible = health.get('eligible_codes', [])
    verified = sorted(set(health.get('trusted_codes', [])) & set(health.get('trusted_history_codes', [])))
    if limited:
        now = exchange_now()
        expected = _expected_session(now).isoformat()
        if (verified and health.get('trusted_trade_date') == health.get('trade_date') == expected
                and calendar_verified(now.date()) and not settings.engine.use_intraday_factors):
            eligible = verified
            # An incomplete market universe may support observed holdings analysis,
            # but it cannot authorize any buy/add order or funded stock selection.
            settings = replace(settings, risk=replace(settings.risk, max_total_position=0.))
        else:
            return dict(status='blocked', reason=health.get('reason','行情未通过核验'), data_health=health,
                    trade_date=health.get('trade_date'), plan=[], orders=[], holdings=[], evidence=[])
    date = health['trade_date']
    positions = holdings.load_positions(settings.data.db_path)
    codes = sorted(set(eligible) | set(positions.code))
    names = db.load_spot(settings.data.db_path)
    name_map = dict(zip(names.code,names.name)) if not names.empty else {}
    name_map.update(dict(zip(positions.code,positions.name)))
    frames = []
    for i, code in enumerate(codes):
        progress('计算历史因子与最新交易日数据',i,len(codes))
        bars = db.load_training_bars(settings.data.db_path,code,end=date,adjustment=settings.data.adjust)
        if len(bars) >= 61:
            bars['name'] = name_map.get(code,code)
            frames.append(compute_features(bars).iloc[60:])
    features = pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()
    bench = db.load_training_bars(settings.data.db_path,settings.data.index_symbol,end=date,adjustment='none')
    if features.empty or bench.empty:
        return dict(status='blocked',reason='最新交易日缺少可计算的可信历史因子或基准',trade_date=date,
                    plan=[],orders=[],holdings=frame_records(positions),evidence=[])
    today = features[pd.to_datetime(features.date) == pd.Timestamp(date)]
    positions = positions.merge(today[['code','close']].rename(columns={'close':'price'}),on='code',how='left')
    ledger = PredictionLedger(settings.data.db_path)
    try:
        frozen = ledger.get(date)
    finally:
        ledger.close()
    weight = frozen['w_a'] if frozen else settings.engine.w_a
    progress('结合当前持仓、成本、现金与仓位约束生成方案')
    decision = decide(features,bench,date,settings,weight,positions,allow_new_entries=not limited)
    payload = decision.to_payload()
    selected = list(dict.fromkeys(list(positions.code)+list(decision.plan.code)))
    return dict(status='limited' if limited else 'ready',
        new_entries_allowed=not limited, reason=health.get('reason',''), data_health=health,
        verified_stock_count=len(eligible), excluded_stock_count=max(0,health.get('expected_codes',len(eligible))-len(eligible)),
        trade_date=date,generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        source=health.get('source'),coverage=health.get('coverage'),cash=settings.account.cash,
        holdings=frame_records(positions),plan=payload['plan'],orders=payload['orders'],decision=payload,
        evidence=observed_evidence(features,date,selected),
        missing_holdings=list(positions.loc[positions.price.isna(),'code']),
        basis=('全市场核验未通过：仅展示通过核验标的的观察评分和当前持仓风险；新买入、加仓及资金配置暂停。' if limited else '') + '使用截至最新完整交易日的可信历史日线与收盘价；每次读取当前持仓及现金；仅生成方案，不执行订单')
