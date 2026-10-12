"""Behavioral checks for the durable, immutable result boundary."""

from __future__ import annotations

import sqlite3
from copy import deepcopy

import pytest

from services.result_store import ResultStore, ResultStoreError


def payload(rows=None):
    return {
        "rows": rows if rows is not None else [
            {"code": "000001", "name": "A", "price": 20.0, "score_blend": 88.0,
             "price_bracket": "low", "rank": 1, "hand_cost": 2000},
        ],
        "orders": [],
        "data_as_of": "2026-10-12T09:00:00+08:00",
        "generated_at": "2026-10-12T09:30:00+08:00",
        "trade_date": "2026-10-12",
        "versions": {"data": "d1", "strategy": "s1", "parameters": "p1"},
        "valid_until": "2026-10-12T16:10:00+08:00",
        "allow_new_buy": True,
        "selection": {"funnel": []},
    }


def test_read_does_not_initialize_and_stage_is_invisible(tmp_path):
    directory = tmp_path / "missing"
    store = ResultStore(directory)
    assert store.read_view() == {
        "snapshot": None, "revocation": None, "audit": None, "job": None,
        "response_revision": 0,
    }
    assert not directory.exists()
    snapshot_id = store.stage(payload())
    assert (directory / "results.db").exists()
    assert ResultStore(directory).read_view()["snapshot"] is None
    assert snapshot_id


def test_relative_artifact_path_survives_write_and_cwd_change(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = ResultStore("artifacts")
    assert store.read_view()["snapshot"] is None
    assert not (tmp_path / "artifacts").exists()
    snapshot_id = store.stage(payload())
    store.publish(snapshot_id)
    store.save_job({"job_id": "relative-job", "status": "queued", "stage": "queued"})
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    assert store.read_view()["snapshot"]["snapshot_id"] == snapshot_id
    assert store.get_job("relative-job")["status"] == "queued"
    assert ResultStore(tmp_path / "artifacts").read_view()["snapshot"]["snapshot_id"] == snapshot_id


def test_publish_changes_head_and_job_atomically_across_instances(tmp_path):
    store = ResultStore(tmp_path)
    first = store.stage(payload())
    store.publish(first, expected_versions=payload()["versions"])
    old = ResultStore(tmp_path).read_view()
    assert old["snapshot"]["snapshot_id"] == first
    assert old["response_revision"] == 1
    store.save_job({"job_id": "j2", "status": "running", "stage": "publish",
                    "created_at": "2026-10-12T09:00:00+08:00", "started_at": "2026-10-12T09:01:00+08:00"})
    second_payload = payload(rows=[])
    second_payload["versions"]["data"] = "d2"
    second = store.stage(second_payload, job_id="j2")
    assert ResultStore(tmp_path).read_view()["snapshot"]["snapshot_id"] == first
    revision = store.publish(second, expected_versions=second_payload["versions"], job_id="j2")
    view = ResultStore(tmp_path).read_view()
    assert view["response_revision"] == revision
    assert view["snapshot"]["snapshot_id"] == second
    assert view["snapshot"]["rows"] == []
    assert view["job"]["status"] == "succeeded"
    assert view["job"]["stage"] == "complete"
    assert view["job"]["finished_at"] is not None
    assert store.get_job("j2")["status"] == "succeeded"
    with pytest.raises(KeyError):
        store.get_job("unknown")


@pytest.mark.parametrize("change", [
    lambda p: p.pop("generated_at"),
    lambda p: p["rows"][0].update(price=float("nan")),
    lambda p: p["rows"].append(deepcopy(p["rows"][0])),
    lambda p: p["rows"][0].update(code="bad"),
    lambda p: p["rows"][0].update(price=0),
    lambda p: p["rows"][0].update(price_bracket="top"),
    lambda p: p.update(generated_at="2026-10-12T09:30:00"),
    lambda p: p.update(snapshot_id="caller-controlled"),
])
def test_stage_rejects_invalid_payload(tmp_path, change):
    data = payload()
    change(data)
    with pytest.raises((ValueError, TypeError)):
        ResultStore(tmp_path).stage(data)


def test_revocation_is_sticky_for_same_identity_and_keeps_head(tmp_path):
    store = ResultStore(tmp_path)
    data = payload()
    first = store.stage(data)
    store.publish(first)
    revision = store.revoke(first, {"code": "source_revoked", "message": "source withdrawn"})
    view = ResultStore(tmp_path).read_view()
    assert view["snapshot"]["snapshot_id"] == first
    assert view["revocation"]["code"] == "source_revoked"
    assert store.revoke(first, {"code": "other", "message": "other"}) == revision
    duplicate = store.stage(data)
    with pytest.raises((ValueError, ResultStoreError)):
        store.publish(duplicate)
    assert ResultStore(tmp_path).read_view()["response_revision"] == revision


def test_revoked_input_versions_cannot_be_republished_with_new_metadata_or_rows(tmp_path):
    store = ResultStore(tmp_path)
    first = store.stage(payload())
    store.publish(first)
    store.revoke(first, {"code": "source_revoked", "message": "source withdrawn"})
    changed = payload(rows=[])
    changed["generated_at"] = "2026-10-12T10:00:00+08:00"
    changed["trade_date"] = "2026-10-13"
    duplicate = store.stage(changed)
    with pytest.raises((ValueError, ResultStoreError)):
        store.publish(duplicate)
    changed["versions"]["data"] = "d2"
    corrected = store.stage(changed)
    store.publish(corrected)
    assert store.read_view()["snapshot"]["snapshot_id"] == corrected


def test_publish_rejects_tampered_staged_payload_before_changing_head(tmp_path):
    store = ResultStore(tmp_path)
    first = store.stage(payload())
    store.publish(first)
    staged = store.stage(payload(rows=[]))
    with sqlite3.connect(tmp_path / "results.db") as db:
        db.execute("UPDATE result_snapshots SET canonical_payload='{}' WHERE snapshot_id=?", (staged,))
    with pytest.raises((ValueError, ResultStoreError)):
        store.publish(staged)
    assert store.read_view()["snapshot"]["snapshot_id"] == first


def test_rejected_publish_preserves_old_head(tmp_path):
    store = ResultStore(tmp_path)
    first = store.stage(payload())
    store.publish(first)
    second_data = payload(rows=[])
    second = store.stage(second_data)
    with pytest.raises((ValueError, ResultStoreError)):
        store.publish(second, expected_versions={"data": "different"})
    assert store.read_view()["snapshot"]["snapshot_id"] == first


def test_audit_failure_and_job_are_persisted_without_changing_result(tmp_path):
    store = ResultStore(tmp_path)
    snapshot_id = store.stage(payload())
    store.publish(snapshot_id)
    audit = {"state": "failed", "data_version": "d1", "parameters_version": "p1",
             "checked_at": "2026-10-12T10:00:00+08:00", "valid_until": None,
             "error": {"code": "source_error", "message": "offline", "retryable": True},
             "ready": False}
    revision = store.record_audit(audit)
    view = ResultStore(tmp_path).read_view()
    assert view["snapshot"]["snapshot_id"] == snapshot_id
    assert view["audit"] == audit
    assert view["response_revision"] == revision
    assert store.latest_job() is None


def test_audit_rejects_incomplete_certificate(tmp_path):
    store = ResultStore(tmp_path)
    with pytest.raises(ValueError):
        store.record_audit({"state": "passed", "data_version": "d1", "ready": True})
    assert store.read_view()["audit"] is None


@pytest.mark.parametrize("funnel", [
    [{"name": "a", "input": 3, "output": 2, "excluded_by_reason": {"bad": 2}}],
    [{"name": "a", "input": 3, "output": 2, "excluded_by_reason": {"bad": 1}},
     {"name": "b", "input": 3, "output": 1, "excluded_by_reason": {"bad": 2}}],
    [{"name": "a", "input": 3, "output": 2, "excluded_by_reason": {"bad": 1}}],
])
def test_stage_rejects_false_funnel_evidence(tmp_path, funnel):
    data = payload()
    data["selection"]["funnel"] = funnel
    with pytest.raises(ValueError):
        ResultStore(tmp_path).stage(data)


def test_stage_accepts_continuous_funnel_ending_at_row_count(tmp_path):
    data = payload()
    data["selection"]["funnel"] = [
        {"name": "a", "input": 3, "output": 2, "excluded_by_reason": {"bad": 1}},
        {"name": "b", "input": 2, "output": 1, "excluded_by_reason": {"bad": 1}},
    ]
    snapshot_id = ResultStore(tmp_path).stage(data)
    assert snapshot_id


def test_stage_rejects_malformed_orders_and_accepts_sell_order(tmp_path):
    sell = {"code": "000001", "name": "A", "action": "止盈卖出", "shares": 100,
            "price_ref": 20.0, "limit_low": 20.0, "limit_high": 20.2,
            "amount": 2000.0, "weight": 0.0, "stop_loss": 19.0,
            "take_profit": 20.0, "reason": "target", "warning": "sell"}
    for invalid in (1, {"code": "000001", "action": "买入"},
                    dict(sell, price_ref=float("nan")), dict(sell, limit_high=19.0),
                    dict(sell, shares=0)):
        data = payload()
        data["orders"] = [invalid]
        with pytest.raises((ValueError, TypeError)):
            ResultStore(tmp_path).stage(data)
    valid = payload()
    valid["orders"] = [sell]
    assert ResultStore(tmp_path).stage(valid)


def test_stage_accepts_recap_actions_and_rejects_unknown_action(tmp_path):
    order = {"code": "000001", "name": "A", "action": "买入", "shares": 100,
             "price_ref": 20.0, "limit_low": 19.9, "limit_high": 20.1,
             "amount": 2000.0, "weight": 0.0, "stop_loss": 19.0,
             "take_profit": 21.0, "reason": "test", "warning": "test"}
    store = ResultStore(tmp_path)
    for action in ("买入", "加仓", "清仓", "止盈卖出"):
        data = payload()
        data["orders"] = [dict(order, action=action)]
        assert store.stage(data)
    data = payload()
    data["orders"] = [dict(order, action="launch")]
    with pytest.raises(ValueError):
        store.stage(data)


def test_corrupt_database_or_payload_raises_read_error(tmp_path):
    store = ResultStore(tmp_path)
    snapshot_id = store.stage(payload())
    store.publish(snapshot_id)
    with sqlite3.connect(tmp_path / "results.db") as db:
        db.execute("UPDATE result_snapshots SET canonical_payload = '{}' WHERE snapshot_id = ?", (snapshot_id,))
    with pytest.raises(ResultStoreError):
        ResultStore(tmp_path).read_view()
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "results.db").write_bytes(b"not a sqlite database")
    with pytest.raises(ResultStoreError):
        ResultStore(broken).read_view()


def test_staged_head_pointer_cannot_be_read_as_published(tmp_path):
    store = ResultStore(tmp_path)
    staged = store.stage(payload())
    with sqlite3.connect(tmp_path / "results.db") as db:
        db.execute("INSERT INTO result_head (id, snapshot_id) VALUES (1, ?)", (staged,))
    with pytest.raises(ResultStoreError):
        store.read_view()


def test_missing_head_target_or_revocation_ledger_is_a_read_error(tmp_path):
    store = ResultStore(tmp_path)
    first = store.stage(payload())
    store.publish(first)
    with sqlite3.connect(tmp_path / "results.db") as db:
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("UPDATE result_head SET snapshot_id='missing' WHERE id=1")
    with pytest.raises(ResultStoreError):
        store.read_view()
    with sqlite3.connect(tmp_path / "results.db") as db:
        db.execute("UPDATE result_head SET snapshot_id=? WHERE id=1", (first,))
        db.execute("UPDATE result_snapshots SET status='revoked' WHERE snapshot_id=?", (first,))
    with pytest.raises(ResultStoreError):
        store.read_view()
