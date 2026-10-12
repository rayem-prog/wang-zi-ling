"""Immutable prediction snapshots; mature future returns reward each snapshot once."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

from blend.weights import adjusted_weights
from strategy.decision import asof_frame


class PredictionLedger:
    def __init__(self, path=':memory:', namespace='formal-v1', *, check_same_thread=True):
        if path != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=30, check_same_thread=check_same_thread)
        self.namespace = namespace
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS prediction_snapshots (
                namespace TEXT, date TEXT, payload TEXT NOT NULL,
                PRIMARY KEY(namespace,date));
            CREATE TABLE IF NOT EXISTS prediction_evaluations (
                namespace TEXT, date TEXT, mature_date TEXT, payload TEXT NOT NULL,
                PRIMARY KEY(namespace,date));
        ''')

    def close(self):
        self.conn.close()

    def get(self, date):
        row = self.conn.execute('SELECT payload FROM prediction_snapshots WHERE namespace=? AND date=?',
                                (self.namespace, str(date))).fetchone()
        return json.loads(row[0]) if row else None

    def freeze(self, date, payload):
        with self.conn:
            self.conn.execute('INSERT OR IGNORE INTO prediction_snapshots VALUES (?,?,?)',
                              (self.namespace, str(date), json.dumps(payload, sort_keys=True, allow_nan=False)))
        return self.get(date)

    def advance(self, features, bench, as_of, engine_settings, horizon=5):
        """Close-to-close prediction efficacy, distinct from portfolio execution P&L."""
        if horizon < 1:
            raise ValueError('评价周期必须为正数')
        stock = asof_frame(features, as_of)
        benchmark = asof_frame(bench, as_of)
        days = benchmark.date.tolist()
        # Missing benchmark sessions cannot shorten the five-session horizon.
        if days:
            from data.trading_calendar import expected_sessions
            days = [pd.Timestamp(d).date() for d in expected_sessions(str(days[0]), str(days[-1]))]
        prices = stock.set_index(['date', 'code']).close.to_dict() if not stock.empty else {}
        bprices = benchmark.set_index('date').close.to_dict() if not benchmark.empty else {}
        conn = self.conn
        conn.execute('BEGIN IMMEDIATE')
        try:
            row = conn.execute("SELECT payload FROM prediction_evaluations WHERE namespace=? AND coalesce(json_extract(payload,'$.applied_as_of'),mature_date)<=? "
                               'ORDER BY rowid DESC LIMIT 1', (self.namespace, str(as_of))).fetchone()
            wa = json.loads(row[0])['new_w_a'] if row else engine_settings.w_a
            if engine_settings.manual_override and engine_settings.manual_w_a is not None:
                wa = min(engine_settings.w_max, max(engine_settings.w_min, engine_settings.manual_w_a))
            result = dict(old_w_a=wa, old_w_b=round(1-wa,4), new_w_a=wa, new_w_b=round(1-wa,4),
                          weight_delta=0., r_a=0., r_b=0., er_a=0., er_b=0.,
                          win_rate_a=0., win_rate_b=0., bench_return=0., evaluated_count=0,
                          summary='没有新成熟的冻结预测，维持权重。')
            if engine_settings.manual_override:
                result['summary'] = '人工权重覆盖已启用，不自动调权。'
                conn.commit()
                return result
            pending = conn.execute('SELECT s.date,s.payload FROM prediction_snapshots s '
                                   'LEFT JOIN prediction_evaluations e ON s.namespace=e.namespace AND s.date=e.date '
                                   'WHERE s.namespace=? AND s.date<=? AND e.date IS NULL ORDER BY s.date',
                                   (self.namespace, str(as_of))).fetchall()
            for date, raw in pending:
                origin = pd.Timestamp(date).date()
                if origin not in days:
                    continue
                p = days.index(origin)
                if p+horizon >= len(days):
                    break
                period = days[p:p+horizon+1]
                sample = pd.DataFrame(json.loads(raw)['scores'])
                if sample.empty:
                    continue
                n = min(len(sample), 15, max(3, int(len(sample)*.2)))
                groups = [sample.sort_values([f'score_{e}', 'code'], ascending=[False,True], kind='stable').head(n)
                          for e in ('a', 'b')]
                codes = set(groups[0].code) | set(groups[1].code)
                if any(not pd.notna(bprices.get(d)) or bprices.get(d,0) <= 0 for d in period):
                    continue
                if any(not pd.notna(prices.get((d,c))) or prices.get((d,c),0) <= 0 for d in period[1:] for c in codes):
                    continue
                anchors = sample.set_index('code').close.to_dict()
                if any(not pd.notna(anchors.get(c)) or anchors.get(c,0) <= 0 for c in codes):
                    continue
                # Both the prediction and its reference price were frozen at origin.
                snapshot = json.loads(raw)
                bstart = snapshot.get('benchmark_close')
                if not bstart or bstart <= 0:
                    continue
                rb = bprices[period[-1]]/bstart-1
                rets = [[prices[(period[-1],c)]/anchors[c]-1 for c in g.code] for g in groups]
                ra, rbb = [sum(r)/len(r) for r in rets]
                wins = [sum(v>0 for v in r)/len(r) for r in rets]
                ers = [ra-rb, rbb-rb]
                effective = [max(.0001, er+(win-.5)*.04+.02) for er,win in zip(ers,wins)]
                frozen_engine = snapshot.get('parameters',{}).get('engine',{})
                minimum = frozen_engine.get('w_min',engine_settings.w_min)
                maximum = frozen_engine.get('w_max',engine_settings.w_max)
                new, other = adjusted_weights(*effective, wa, minimum, maximum)
                audit = dict(old_w_a=wa, old_w_b=round(1-wa,4), new_w_a=new, new_w_b=other,
                    weight_delta=round(new-wa,4), r_a=round(ra*100,4), r_b=round(rbb*100,4),
                    er_a=round(ers[0]*100,4), er_b=round(ers[1]*100,4),
                    win_rate_a=round(wins[0]*100,4), win_rate_b=round(wins[1]*100,4),
                    bench_return=round(rb*100,4), prediction_date=date, mature_date=str(period[-1]),
                    horizon=horizon, applied_as_of=str(as_of), samples=[dict(code=c,reference_close=anchors[c],future_close=prices[(period[-1],c)]) for c in sorted(codes)], summary=f'已评价 {date} 冻结预测的未来{horizon}交易日收益。')
                conn.execute('INSERT INTO prediction_evaluations VALUES (?,?,?,?)',
                             (self.namespace,date,str(period[-1]),json.dumps(audit,sort_keys=True)))
                count = result['evaluated_count']+1
                initial = result['old_w_a']
                result = dict(audit, evaluated_count=count, old_w_a=initial, old_w_b=round(1-initial,4),
                              weight_delta=round(new-initial,4))
                wa = new
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise
