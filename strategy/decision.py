"""Pure, point-in-time decision pipeline shared by production and historical replay."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json

import pandas as pd

from blend.weights import merge_scores
from config.settings import Settings
from features.alpha import FACTOR_COLUMNS
from model import dataset, trainer
from strategy import macro, recap, rules, signals, sizing

_MODEL_CACHE: dict[str, object] = {}


def asof_frame(frame: pd.DataFrame, date) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    out = frame.copy()
    out['date'] = pd.to_datetime(out['date']).dt.date
    out = out[out.date <= pd.Timestamp(date).date()]
    keys = ['date', 'code'] if 'code' in out else ['date']
    if out.duplicated(keys).any():
        raise ValueError('决策输入存在重复证券/交易日')
    return out.sort_values(keys, kind='stable').reset_index(drop=True)


def frame_records(frame):
    return json.loads(frame.to_json(orient='records', date_format='iso', double_precision=15))


def restore_frame(rows, columns=None):
    frame = pd.DataFrame(rows, columns=columns)
    if 'date' in frame and not frame.empty:
        frame['date'] = pd.to_datetime(frame.date).dt.date
    return frame


@dataclass
class Decision:
    date: str
    w_a: float
    scores: pd.DataFrame
    plan: pd.DataFrame
    orders: pd.DataFrame
    macro: macro.MacroResult
    rule_audit: dict
    model_audit: dict
    input_digest: str
    parameters: dict

    def to_payload(self):
        return dict(date=self.date, w_a=self.w_a, scores=frame_records(self.scores),
                    plan=frame_records(self.plan), orders=frame_records(self.orders),
                    score_columns=list(self.scores.columns), plan_columns=list(self.plan.columns),
                    macro=asdict(self.macro), rule_audit=self.rule_audit,
                    model_audit=self.model_audit, input_digest=self.input_digest,
                    parameters=self.parameters)

    @classmethod
    def from_payload(cls, data):
        return cls(date=data['date'], w_a=data['w_a'],
                   scores=restore_frame(data['scores'], data['score_columns']),
                   plan=restore_frame(data['plan'], data['plan_columns']),
                   orders=restore_frame(data['orders'], recap.ORDER_COLUMNS),
                   macro=macro.MacroResult(**data['macro']), rule_audit=data['rule_audit'],
                   model_audit=data['model_audit'], input_digest=data['input_digest'],
                   parameters=data['parameters'])


def engine_a(features, bench, date, train_days=750, min_train_rows=5000):
    """Train on a deterministic 20-session anchor, with labels bounded by that anchor."""
    history = asof_frame(features, date)
    if history.empty:
        history = history.reindex(columns=list(dict.fromkeys(list(history.columns) + ['code', 'date'])))
    benchmark = asof_frame(bench, date)
    today = history[history.date == pd.Timestamp(date).date()]
    scores = pd.DataFrame(dict(code=today.code, date=today.date, score_a=50.))
    audit = dict(status='训练样本不足，A引擎使用中性分50', reason='insufficient_training_samples',
                 model_trained=False, top_features=[], training_as_of=None)
    if today.empty:
        audit.update(status='当日无可信个股数据，A引擎无信号', reason='no_current_stock_rows')
        return scores, audit
    if len(benchmark) < 20 or len(history) < min_train_rows:
        return scores, audit
    anchor = benchmark.date.iloc[(len(benchmark)//20)*20-1]
    start = anchor - pd.Timedelta(days=train_days)
    train = history[(history.date >= start) & (history.date <= anchor)].copy()
    train = train.reindex(columns=list(dict.fromkeys(list(train.columns)+FACTOR_COLUMNS)))
    cohort = train.reindex(columns=['date','code','close']+FACTOR_COLUMNS)
    anchored_bench = benchmark[benchmark.date <= anchor][['date','close']]
    key = sha256(pd.util.hash_pandas_object(cohort,index=False).values.tobytes()+
                 pd.util.hash_pandas_object(anchored_bench,index=False).values.tobytes()+
                 f'{anchor}:{min_train_rows}:42'.encode()).hexdigest()
    if key not in _MODEL_CACHE:
        X, y, _ = dataset.build_training_data(train, benchmark, max_date=anchor)
        model = trainer.train_model(X,y,seed=42) if len(X) >= min_train_rows and len(set(y)) >= 2 else None
        if len(_MODEL_CACHE) >= 32:
            _MODEL_CACHE.pop(next(iter(_MODEL_CACHE)))
        _MODEL_CACHE[key] = model
    model = _MODEL_CACHE[key]
    if model is None:
        return scores, audit
    Xt = today.reindex(columns=FACTOR_COLUMNS).fillna(0.).astype(float).to_numpy()
    scores['score_a'] = trainer.predict_scores(model, Xt)
    return scores, dict(status='已用成熟历史标签训练', reason=None, model_trained=True, training_as_of=str(anchor),
                        model_digest=key, seed=42,
                        top_features=trainer.get_feature_importances(model, FACTOR_COLUMNS)[:5])


def decide(features, bench, as_of, settings: Settings, w_a: float,
           current_holdings=None, *, train_days=750, min_train_rows=5000, engine='blend', events_cfg=None, allow_new_entries=True) -> Decision:
    date = pd.Timestamp(as_of).date()
    history = asof_frame(features, date)
    if history.empty:
        history = history.reindex(columns=list(dict.fromkeys(list(history.columns) + ['code', 'date', 'close', 'vol_20'])))
    benchmark = asof_frame(bench, date)
    today = history[history.date == date].copy()
    today = today.sort_values('code', kind='stable').reset_index(drop=True)
    quotes = pd.DataFrame(columns=['code', 'name', 'price', 'pct_change'])
    if not today.empty:
        quotes = today[['code', 'close']].rename(columns={'close': 'price'})
        quotes['name'] = today['name'] if 'name' in today else today.code
        quotes['pct_change'] = today['ret_1'].fillna(0.)*100 if 'ret_1' in today else 0.
    events = macro.load_event_calendar() if events_cfg is None else events_cfg
    policy = macro.compute_macro_score({settings.data.index_symbol: benchmark}, quotes, events_cfg=events, current_date=date)
    rule_cfg, rule_audit = rules.optimize_rules_formula(policy.stance, today)
    if engine == 'b':
        sa = pd.DataFrame(dict(code=today.code, date=today.date, score_a=50.))
        model_audit = dict(status='纯B规则引擎，A引擎未运行', reason='pure_b_engine',
                           model_trained=False, top_features=[], training_as_of=None)
    else:
        sa, model_audit = engine_a(history, benchmark, date, train_days, min_train_rows)
    sb = rules.score_engine_b(today, config=rule_cfg)
    if sb.empty:
        sb = pd.DataFrame(columns=['code', 'date', 'score_b'])
    base = today.reindex(columns=['code', 'date', 'close', 'vol_20'])
    if today.empty:
        scores = pd.DataFrame(columns=['code', 'date', 'close', 'vol_20', 'score_a', 'score_b'])
    else:
        scores = base.merge(sa, on=['code', 'date'], how='left').merge(sb, on=['code', 'date'], how='left')
    scores[['score_a', 'score_b']] = scores[['score_a', 'score_b']].fillna(50.)
    scores = merge_scores(scores, w_a)
    if engine in ('a', 'b'):
        scores['score_blend'] = scores[f'score_{engine}']
    rec = signals.build_recommendations(scores, quotes)
    rec = rec.sort_values(['score_blend', 'code'], ascending=[False, True], kind='stable')
    median_vol = float(today.vol_20.median()) if 'vol_20' in today and not today.empty else .02
    if pd.isna(median_vol):
        median_vol = .02
    # Rank before applying max_positions; keep held names for sell decisions/valuation.
    held = set(current_holdings.code) if current_holdings is not None and not current_holdings.empty else set()
    eligible = set(rec.head(settings.account.max_positions).code) | held
    rec = rec[rec.code.isin(eligible)].reset_index(drop=True)
    plan = sizing.compute_plan(rec, median_vol, settings, policy, current_holdings)
    # Without any current stock quotes, held prices can only support stale valuation.
    orders = (pd.DataFrame(columns=recap.ORDER_COLUMNS) if today.empty else
              recap.generate_daily_orders(plan, current_holdings, settings, policy))
    # A stale held mark still contributes to valuation, never to a new order.
    if not orders.empty:
        prices = pd.to_numeric(today['close'], errors='coerce')
        valid_prices = prices.gt(0) & ~prices.isin([float('inf'), float('-inf')])
        current_codes = set(today.loc[valid_prices, 'code'])
        orders = orders[orders.code.isin(current_codes)].reset_index(drop=True)
    if not allow_new_entries:
        orders = orders[~orders.action.isin(['买入','加仓'])].reset_index(drop=True)
    parameters = dict(allow_new_entries=allow_new_entries, risk=asdict(settings.risk), account=asdict(settings.account),
                      engine=asdict(settings.engine), train_days=train_days,
                      min_train_rows=min_train_rows, score_engine=engine,
                      events=events)
    # Hash canonical frames directly: no repeated JSON copies of all historical rows.
    data_hash = sha256()
    for frame in (history, benchmark, current_holdings):
        if frame is None:
            data_hash.update(b'no-frame')
        else:
            canonical = frame.sort_index(axis=1)
            if frame is current_holdings and 'code' in canonical:
                canonical = canonical.sort_values('code',kind='stable')
            data_hash.update(json.dumps(list(canonical.columns)).encode())
            data_hash.update(pd.util.hash_pandas_object(canonical,index=False).values.tobytes())
    data_hash.update(json.dumps(dict(parameters=parameters,w_a=w_a),sort_keys=True).encode())
    digest = data_hash.hexdigest()
    return Decision(str(date), w_a, scores, plan, orders, policy, rule_audit, model_audit, digest, parameters)
