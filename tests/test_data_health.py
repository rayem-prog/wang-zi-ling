import datetime as dt
import json
from dataclasses import replace

import pandas as pd

from config.settings import Settings
from data import db
from data.health import _expected_session, assess_data_health, recommendation_state
from data.security_master import inactive_status
from data.trading_calendar import closure_kind, contains_expected_session


def _bar(code, day):
    return pd.DataFrame([{"code": code, "date": day, "open": 10.0, "high": 10.0,
                          "low": 10.0, "close": 10.0, "volume": 100.0, "amount": 1000.0}])


def test_health_blocks_missing_trade_day_coverage_and_reports_provenance(tmp_path):
    s = replace(Settings(), data=replace(Settings().data, db_path=str(tmp_path / "market.db")),
                paths=replace(Settings().paths, artifact_dir=str(tmp_path / "artifacts")))
    db.init_db(s.data.db_path)
    day = dt.date(2026, 9, 25)
    db.save_bars(s.data.db_path, "sh000300", _bar("sh000300", day), source="tencent")
    db.save_bars(s.data.db_path, "000001", _bar("000001", day), source="tencent")
    health = assess_data_health(s, now=dt.datetime(2026, 9, 27, 12),
                                expected_codes=["000001", "000002"])
    assert health["trade_date"] == "2026-09-25"
    assert health["coverage"] == 0.5
    assert health["minute_coverage"] == 0.0
    assert health["minute_missing_codes"] == ["000001", "000002"]
    assert health["missing_codes"] == ["000002"]
    assert health["source"] == "tencent"
    assert health["updated_at"]
    assert health["ready"] is False
    assert health["calendar_status"] == "non_trading_day"
    assert health["classifications"]["possible_suspension_or_gap"] == ["000002"]
    assert "000001" in health["classifications"]["historical_short"]


def test_recommendation_requires_matching_metadata(tmp_path):
    s = replace(Settings(), paths=replace(Settings().paths, artifact_dir=str(tmp_path)))
    health = {"ready": True, "trade_date": "2026-09-25", "reason": ""}
    assert recommendation_state(s, health)["ready"] is False
    (tmp_path / "recommendations.csv").write_text("code,price\n000001,10\n")
    (tmp_path / "latest_recommendations.meta.json").write_text(json.dumps({
        "trade_date": "2026-09-24", "source": "tencent", "generated_at": "2026-09-24T17:00:00",
        "coverage": 1.0,
    }))
    assert recommendation_state(s, health)["ready"] is False


def test_official_mid_autumn_market_closure_is_not_a_missing_trading_day(tmp_path):
    s = replace(Settings(), data=replace(Settings().data, db_path=str(tmp_path / "market.db")))
    db.init_db(s.data.db_path)
    day = dt.date(2026, 9, 24)
    db.save_bars(s.data.db_path, "sh000300", _bar("sh000300", day), source="tencent")
    db.save_bars(s.data.db_path, "000001", _bar("000001", day), source="tencent")
    assert _expected_session(dt.datetime(2026, 9, 25, 17)) == day
    assert _expected_session(dt.datetime(2026, 9, 27, 17)) == day
    assert _expected_session(dt.datetime(2026, 9, 28, 9)) == day
    health = assess_data_health(s, now=dt.datetime(2026, 9, 25, 17),
                                expected_codes=["000001"])
    assert health["calendar_status"] == "exchange_holiday"
    assert "基准指数停在" not in health["reason"]


def test_health_classifies_verified_suspensions_and_delistings_out_of_current_pool(tmp_path):
    s = replace(Settings(), data=replace(Settings().data, db_path=str(tmp_path / "market.db")))
    db.init_db(s.data.db_path)
    day = dt.date(2026, 9, 24)
    db.save_bars(s.data.db_path, "sh000300", _bar("sh000300", day), source="tencent")
    db.save_bars(s.data.db_path, "000001", _bar("000001", day), source="tencent")

    health = assess_data_health(
        s,
        now=dt.datetime(2026, 9, 28, 9),
        expected_codes=["000001", "600837", "601059", "601238", "601989"],
    )

    assert health["pool_codes"] == 5
    assert health["expected_codes"] == 1
    assert health["covered_codes"] == 1
    assert health["missing_codes"] == []
    assert {entry["code"] for entry in health["excluded_codes"]} == {
        "600837", "601059", "601238", "601989"
    }
    assert {entry["code"] for entry in health["classifications"]["confirmed_suspensions"]} == {
        "601059", "601238"
    }
    assert {entry["code"] for entry in health["classifications"]["delisted"]} == {
        "600837", "601989"
    }


def test_known_listing_date_prevents_false_historical_gap(tmp_path):
    s = replace(Settings(), data=replace(Settings().data, db_path=str(tmp_path / "market.db")))
    db.init_db(s.data.db_path)
    listing = dt.date(2023, 2, 1)
    dates = pd.bdate_range(listing, periods=61)
    rows = pd.DataFrame([{
        "code": "601059", "date": date.date(), "open": 10.0, "high": 10.0,
        "low": 10.0, "close": 10.0, "volume": 100.0, "amount": 1000.0,
    } for date in dates])
    db.save_bars(s.data.db_path, "601059", rows, source="tencent")

    health = assess_data_health(
        s, now=dt.datetime(2026, 9, 10, 17), expected_codes=["601059"]
    )

    assert health["history_coverage"] == 1.0
    assert "601059" not in health["classifications"]["historical_short"]


def test_historical_exchange_closure_is_not_counted_as_an_expected_session():
    assert closure_kind(dt.date(2023, 1, 2)) == "exchange_holiday"
    assert contains_expected_session("2023-01-01", "2023-01-02") is False


def test_verified_post_2021_listings_do_not_count_as_missing_history(tmp_path):
    s = replace(Settings(), data=replace(Settings().data, db_path=str(tmp_path / "market.db")))
    db.init_db(s.data.db_path)
    listing_dates = {
        "300957": "2021-03-25", "300979": "2021-04-26", "301269": "2022-07-29",
        "600905": "2021-06-10", "600938": "2022-04-21", "600941": "2022-01-05",
        "601728": "2021-08-20", "601868": "2021-09-28", "605117": "2021-04-20",
        "605499": "2021-05-27", "688041": "2022-08-12", "688187": "2021-09-07",
        "688223": "2022-01-26", "688271": "2022-08-22", "688303": "2021-07-22",
    }
    db.save_bars(s.data.db_path, "sh000300", _bar("sh000300", "2026-09-24"), source="tencent")
    for code, listed_on in listing_dates.items():
        rows = pd.DataFrame([{
            "code": code, "date": day.date(), "open": 10.0, "high": 10.0,
            "low": 10.0, "close": 10.0, "volume": 100.0, "amount": 1000.0,
        } for day in pd.bdate_range(listed_on, periods=61)])
        db.save_bars(s.data.db_path, code, rows, source="tencent")

    health = assess_data_health(
        s, now=dt.datetime(2026, 9, 28, 9), expected_codes=list(listing_dates)
    )

    assert health["history_coverage"] == 1.0
    assert health["classifications"]["historical_short"] == []
    assert health["classifications"]["suspected_truncation"] == []


def test_haitong_delisting_gap_is_classified_as_suspension_after_final_session():
    assert inactive_status("600837", "2025-02-05") is None
    suspended = inactive_status("600837", "2025-02-06")
    assert suspended is not None
    assert suspended["status"] == "suspended"
    assert suspended["effective_date"] == "2025-02-06"
    delisted = inactive_status("600837", "2025-03-04")
    assert delisted is not None
    assert delisted["status"] == "delisted"


def test_audit_uses_collected_universe_and_keeps_old_quotes(tmp_path, monkeypatch):
    from data.pipeline import fetch_universe
    from api.health_cache import market_signature
    s = replace(Settings(), data=replace(Settings().data, db_path=str(tmp_path / 'market.db')))
    db.init_db(s.data.db_path)
    db.save_spot(s.data.db_path, pd.DataFrame([
        dict(code=code,name=code,price=10,pct_change=0,volume=100,amount=1000)
        for code in ['000001','000002']]))
    before = market_signature(s)
    monkeypatch.setattr('data.fetcher.fetch_universe', lambda **kw: ['000001'])
    assert fetch_universe(s) == ['000001']
    health = assess_data_health(s, now=dt.datetime(2026, 9, 28, 17))
    assert health['eligible_codes'] == ['000001']
    assert market_signature(s) != before
    assert set(db.load_spot(s.data.db_path).code) == {'000001','000002'}
    # Config changes cannot certify against a pool from the previous configuration.
    changed = replace(s, universe=replace(s.universe, extra_codes=('000003',)))
    other = assess_data_health(changed, now=dt.datetime(2026, 9, 28, 17))
    assert other['error_code'] == 'universe_uncollected'
    assert other['ready'] is False
    assert all(not section['ready'] for section in other['conditions'].values())


def test_collection_pool_failure_preserves_previous_scope(tmp_path, monkeypatch):
    import pytest
    from data.pipeline import fetch_universe
    from api.health_cache import market_signature
    s = replace(Settings(), data=replace(Settings().data, db_path=str(tmp_path / 'market.db')))
    monkeypatch.setattr('data.fetcher.fetch_universe', lambda **kw: ['000001'])
    fetch_universe(s)
    before = market_signature(s)
    monkeypatch.setattr('data.fetcher.fetch_universe', lambda **kw: [])
    with pytest.raises(ValueError, match='证券池为空'):
        fetch_universe(s)
    assert market_signature(s) == before
    health = assess_data_health(s, now=dt.datetime(2026, 9, 28, 17))
    assert health['eligible_codes'] == ['000001']
    db.save_spot(s.data.db_path, pd.DataFrame([
        dict(code='000002',name='old',price=10,pct_change=0,volume=100,amount=1000)]))
    assert market_signature(s) == before
