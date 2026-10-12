"""Result response state and filtering are derived from one persisted view."""

from datetime import datetime

import pytest

from config.settings import Settings, settings_version
from services.result_contract import build_snapshot_response


NOW = datetime.fromisoformat("2026-10-12T10:00:00+08:00")


def rows():
    return [
        {"code": code, "name": name, "price": price, "score_blend": score,
         "price_bracket": bracket, "rank": rank, "hand_cost": price * 100,
         "why_recommended": "test"}
        for rank, (code, name, price, score, bracket) in enumerate([
            ("000001", "one", 20, 90, "low"),
            ("000002", "two", 50, 80, "mid"),
            ("000003", "three", 100, 80, "high"),
            ("000004", "four", 101, 70, "top"),
        ], 1)
    ]


def view(settings=None, data_rows=None):
    settings = settings or Settings()
    return {
        "snapshot": {
            "snapshot_id": "s1", "rows": rows() if data_rows is None else data_rows,
            "orders": [], "data_as_of": "2026-10-12T09:00:00+08:00",
            "generated_at": "2026-10-12T09:30:00+08:00", "trade_date": "2026-10-12",
            "versions": {"data": "d1", "strategy": "s1", "parameters": settings_version(settings)},
            "valid_until": "2026-10-12T16:10:00+08:00",
            "allow_new_buy": True, "selection": {"funnel": [
                {"name": "liquidity", "input": 5, "output": 4,
                 "excluded_by_reason": {"illiquid": 1}},
            ]},
        },
        "revocation": None,
        "audit": {"state": "passed", "data_version": "d1",
                  "parameters_version": settings_version(settings),
                  "checked_at": "2026-10-12T09:40:00+08:00",
                  "valid_until": "2026-10-12T16:10:00+08:00", "error": None, "ready": True},
        "job": None, "response_revision": 7,
    }


def test_none_response_has_complete_idle_shape_and_paused_permissions():
    response = build_snapshot_response({"snapshot": None, "revocation": None,
        "audit": None, "job": None, "response_revision": 0}, Settings(), now=NOW)
    assert response["result_state"] == "none"
    assert response["snapshot_id"] is None
    assert response["rows"] == []
    assert response["counts"] == {"snapshot_total": 0, "filtered_total": 0,
                                  "brackets": {"low": 0, "mid": 0, "high": 0, "top": 0}}
    assert response["empty_kind"] == "no_snapshot"
    assert response["job"]["status"] == "idle"
    assert response["job"]["job_id"] is None
    assert response["action_permissions"]["create_buy"] == "paused"
    assert response["blocked_reasons"]


def test_current_filtering_preserves_global_ranks_and_counts():
    settings = Settings()
    response = build_snapshot_response(view(settings), settings, now=NOW,
        filters={"bracket": "mid", "min_price": 20, "max_price": 50, "sort_by": "price_desc",
                 "codes": "000002,000004", "q": "two"})
    assert response["result_state"] == "current"
    assert [row["code"] for row in response["rows"]] == ["000002"]
    assert response["rows"][0]["rank"] == 2
    assert response["rows"][0]["why_recommended"] == "test"
    assert response["counts"] == {"snapshot_total": 4, "filtered_total": 1,
                                  "brackets": {"low": 0, "mid": 1, "high": 0, "top": 0}}
    assert response["selection"]["funnel"][0]["excluded_by_reason"] == {"illiquid": 1}
    assert response["action_permissions"]["view_candidates"] == "allowed"
    assert response["action_permissions"]["create_buy"] == "paused"
    assert response["response_revision"] == 7


def test_empty_and_filter_no_match_are_distinct():
    settings = Settings()
    screened = build_snapshot_response(view(settings, []), settings, now=NOW)
    no_match = build_snapshot_response(view(settings), settings, now=NOW,
                                       filters={"bracket": "low", "q": "no such name"})
    assert (screened["result_state"], screened["empty_kind"]) == ("current", "screened_out")
    assert (no_match["result_state"], no_match["empty_kind"]) == ("current", "filter_no_match")
    assert no_match["counts"]["snapshot_total"] == 4


@pytest.mark.parametrize("change", [
    lambda v: v["snapshot"].update(valid_until="2026-10-12T09:00:00+08:00"),
    lambda v: v["snapshot"].update(legacy_import=True),
    lambda v: v["audit"].update(state="running"),
    lambda v: v["audit"].update(data_version="different"),
    lambda v: v["snapshot"]["versions"].update(parameters="different"),
])
def test_uncertified_results_remain_visible_but_historical(change):
    settings = Settings()
    source = view(settings)
    change(source)
    response = build_snapshot_response(source, settings, now=NOW)
    assert response["result_state"] == "historical"
    assert len(response["rows"]) == 4
    assert response["action_permissions"]["view_candidates"] == "allowed"
    assert response["action_permissions"]["create_buy"] == "paused"


def test_revoked_hides_rows_but_preserves_explanation():
    settings = Settings()
    source = view(settings)
    source["revocation"] = {"code": "source_revoked", "message": "source withdrawn"}
    response = build_snapshot_response(source, settings, now=NOW)
    assert response["result_state"] == "revoked"
    assert response["snapshot_id"] == "s1"
    assert response["rows"] == []
    assert response["counts"]["snapshot_total"] == 0
    assert response["empty_kind"] == "revoked"
    assert set(response["action_permissions"].values()) == {"forbidden"}
    assert any(reason["code"] == "source_revoked" for reason in response["blocked_reasons"])


def test_defense_forbids_new_buy_and_audit_error_stays_visible():
    settings = Settings()
    source = view(settings)
    source["snapshot"]["allow_new_buy"] = False
    source["audit"].update(state="failed", error={"code": "network", "message": "offline",
                                                   "retryable": True})
    response = build_snapshot_response(source, settings, now=NOW)
    assert response["result_state"] == "historical"
    assert response["audit"]["error"]["code"] == "network"
    assert any(reason["code"] == "network" for reason in response["blocked_reasons"])
    assert response["action_permissions"]["create_buy"] == "forbidden"
    assert response["action_permissions"]["add_position"] == "forbidden"


@pytest.mark.parametrize("filters", [
    {"bracket": "bogus"}, {"sort_by": "bogus"}, {"min_price": -1},
    {"min_price": 50, "max_price": 20}, {"codes": "bad"},
])
def test_invalid_filters_are_rejected(filters):
    with pytest.raises(ValueError):
        build_snapshot_response(view(), Settings(), now=NOW, filters=filters)
