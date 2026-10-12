"""Historical holes are isolated, never filled or crossed by live factors."""
import datetime as dt
from dataclasses import replace
import sqlite3

import pandas as pd

from config.settings import Settings
from data import db
from data.health import assess_data_health
from data.trading_calendar import expected_sessions
from features.alpha import compute_features
from model.dataset import add_future_excess
from scripts.daily_update import _features_for_all


def fixture(tmp_path, latest_segment=70):
    path = str(tmp_path / 'market.db')
    db.init_db(path)
    days = expected_sessions('2026-01-05', '2026-10-09')[:71+latest_segment]
    def frame(code, stock=False):
        records = []
        for i, day in enumerate(days):
            if stock and i == 70:
                continue
            price = 10 + i/100 if i < 70 else 100 + i/100
            records.append(dict(code=code, date=day, open=price, high=price+1,
                low=price-1, close=price, volume=100, amount=1000))
        return pd.DataFrame(records)
    for code, adjust in [('000001','qfq'), ('sh000300','none')]:
        db.publish_bar_snapshot(path, code, frame(code, stock=code=='000001'),
            source='fixture', adjustment=adjust, adjustment_version='v1',
            evidence='test://independent-capture', reason='isolated regression')
    settings = replace(Settings(), data=replace(Settings().data, db_path=path, start_date=days[0]))
    return settings, days


def test_segmented_reader_preserves_holes_without_weakening_strict_reader(tmp_path):
    settings, days = fixture(tmp_path)
    assert db.load_training_bars(settings.data.db_path, '000001').empty
    rows = db.load_training_bars(settings.data.db_path, '000001', gap_policy='segments')
    assert len(rows) == len(days)-1
    assert days[70] not in rows.date.astype(str).tolist()
    assert rows.history_segment.value_counts().tolist() == [70,70]
    assert rows.attrs['internal_gaps'][0]['classification'] == 'unexplained_gap'
    # An in-place content change is still blocked in the segmented policy.
    with sqlite3.connect(settings.data.db_path) as conn:
        conn.execute("UPDATE bars SET close=close+0.1 WHERE code='000001' AND date=?", (days[-1],))
    assert db.load_training_bars(settings.data.db_path, '000001', gap_policy='segments').empty


def test_segmented_factors_reset_warmup_and_never_cross_price_jump(tmp_path):
    settings, days = fixture(tmp_path)
    rows = db.load_training_bars(settings.data.db_path, '000001', gap_policy='segments')
    features = compute_features(rows)
    after = features[features.history_segment == 1]
    assert pd.isna(after.ret_1.iloc[0])
    assert pd.isna(after.ret_60.iloc[59])
    assert after.feature_ready.tolist() == [False]*60+[True]*10
    production = _features_for_all(settings, ['000001'])
    assert len(production) == 20
    assert set(production.date.astype(str)) == set(days[60:70] + days[131:141])


def test_health_keeps_98_percent_gate_and_requires_latest_segment_warmup(tmp_path):
    settings, days = fixture(tmp_path)
    now = dt.datetime.combine(dt.date.fromisoformat(days[-1]), dt.time(17))
    health = assess_data_health(settings, now=now)
    assert health['ready'] is True
    assert health['trusted_coverage'] == 1
    assert health['internal_gaps'][0]['classification'] == 'unexplained_gap'
    short, short_days = fixture(tmp_path/'short', latest_segment=10)
    health = assess_data_health(short, now=dt.datetime.combine(dt.date.fromisoformat(short_days[-1]),dt.time(17)))
    assert health['ready'] is False
    assert health['trusted_coverage'] == 0
    assert health['classifications']['insufficient_recent_history'] == ['000001']


def test_future_labels_remain_unavailable_across_a_missing_exchange_session(tmp_path):
    settings, days = fixture(tmp_path)
    stock = db.load_bars(settings.data.db_path, '000001')
    bench = db.load_bars(settings.data.db_path, 'sh000300')
    labeled = add_future_excess(stock, bench)
    boundary = labeled[labeled.date.astype(str).isin(days[65:70])]
    assert len(boundary) == 5 and boundary.label.isna().all()
