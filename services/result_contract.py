"""Construct a complete signals response from one immutable store read."""

from __future__ import annotations

import math
import re
from datetime import datetime

from config.settings import settings_version
from services.action_permissions import evaluate_action_permissions


FILTER_DEFAULTS = {"bracket": "all", "min_price": None, "max_price": None,
                   "sort_by": "score", "codes": None, "q": ""}
JOB_DEFAULTS = {"job_id": None, "status": "idle", "stage": None, "created_at": None,
                "started_at": None, "finished_at": None, "heartbeat_at": None,
                "error": None, "data_only": False}


def _filters(given):
    result = dict(FILTER_DEFAULTS)
    if given:
        unknown = set(given) - set(result)
        if unknown:
            raise ValueError(f"unknown filters: {', '.join(sorted(unknown))}")
        result.update(given)
    if result["bracket"] not in {"all", "low", "mid", "high", "top"}:
        raise ValueError("invalid bracket")
    if result["sort_by"] not in {"score", "price_asc", "price_desc", "hand_cost_asc"}:
        raise ValueError("invalid sort_by")
    for name in ("min_price", "max_price"):
        value = result[name]
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not math.isfinite(value) or value < 0):
            raise ValueError(f"invalid {name}")
    if result["min_price"] is not None and result["max_price"] is not None:
        if result["min_price"] > result["max_price"]:
            raise ValueError("min_price exceeds max_price")
    codes = result["codes"]
    if codes is not None:
        if not isinstance(codes, str) or not codes.strip():
            raise ValueError("invalid codes")
        parts = [part.strip() for part in codes.split(",")]
        if any(re.fullmatch(r"[0-9]{6}", part) is None for part in parts):
            raise ValueError("invalid codes")
        result["codes"] = ",".join(parts)
    if not isinstance(result["q"], str):
        raise ValueError("invalid q")
    return result


def _future(value, now):
    if not value:
        return False
    try:
        deadline = datetime.fromisoformat(value)
        return deadline.tzinfo is not None and deadline.utcoffset() is not None and deadline > now
    except (ValueError, TypeError):
        return False


def _sort_rows(rows, sort_by):
    if sort_by == "price_asc":
        return sorted(rows, key=lambda row: (row["price"], row["code"]))
    if sort_by == "price_desc":
        return sorted(rows, key=lambda row: (-row["price"], row["code"]))
    if sort_by == "hand_cost_asc":
        return sorted(rows, key=lambda row: (row.get("hand_cost") is None,
                                             row.get("hand_cost") or 0, row["code"]))
    return sorted(rows, key=lambda row: (-row["score_blend"], row["code"]))


def build_snapshot_response(view: dict, settings, *, now: datetime, filters=None) -> dict:
    """Pure response projection; callers provide a single transactional store view."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be offset-aware")
    chosen = _filters(filters)
    snapshot = view.get("snapshot")
    revocation = view.get("revocation")
    certificate = view.get("audit") or {}
    job = {key: (view.get("job") or {}).get(key, default) for key, default in JOB_DEFAULTS.items()}
    audit = {key: certificate.get(key) for key in ("data_version", "checked_at", "valid_until", "error")}
    audit_state = certificate.get("state", "unknown")
    if audit_state not in {"unknown", "pending", "running", "passed", "failed"}:
        audit_state = "unknown"

    versions = dict(snapshot["versions"]) if snapshot else {"data": None, "strategy": None, "parameters": None}
    if snapshot is None:
        state = "none"
    elif revocation:
        state = "revoked"
    elif (not snapshot.get("legacy_import") and audit_state == "passed"
          and certificate.get("ready") is True
          and certificate.get("data_version") == versions["data"]
          and certificate.get("parameters_version") == versions["parameters"]
          and versions["parameters"] == settings_version(settings)
          and _future(certificate.get("valid_until"), now)
          and _future(snapshot.get("valid_until"), now)):
        state = "current"
    else:
        state = "historical"

    all_rows = snapshot["rows"] if snapshot and state != "revoked" else []
    visible = list(all_rows)
    if chosen["codes"]:
        code_set = set(chosen["codes"].split(","))
        visible = [row for row in visible if row["code"] in code_set]
    if chosen["bracket"] != "all":
        visible = [row for row in visible if row["price_bracket"] == chosen["bracket"]]
    if chosen["min_price"] is not None:
        visible = [row for row in visible if row["price"] >= chosen["min_price"]]
    if chosen["max_price"] is not None:
        visible = [row for row in visible if row["price"] <= chosen["max_price"]]
    if chosen["q"]:
        query = chosen["q"].casefold()
        visible = [row for row in visible if query in row["code"].casefold()
                   or query in row["name"].casefold()]
    visible = _sort_rows(visible, chosen["sort_by"])
    brackets = {key: 0 for key in ("low", "mid", "high", "top")}
    for row in visible:
        brackets[row["price_bracket"]] += 1
    if state == "none":
        empty_kind = "no_snapshot"
    elif state == "revoked":
        empty_kind = "revoked"
    elif not all_rows:
        empty_kind = "screened_out" if state == "current" else None
    elif not visible:
        empty_kind = "filter_no_match"
    else:
        empty_kind = None
    permissions, reasons = evaluate_action_permissions(
        state, has_rows=bool(visible),
        allow_new_buy=(certificate.get("allow_new_buy") if state == "current" and isinstance(certificate.get("allow_new_buy"), bool)
                       else snapshot.get("allow_new_buy") if snapshot else None),
        operation_certified=state == "current" and isinstance(certificate.get("allow_new_buy"), bool),
        has_orders=bool(snapshot.get("orders")) if snapshot else False,
        revocation=revocation,
    )
    paused_actions = [action for action in ("create_buy", "add_position", "export_orders")
                      if permissions[action] == "paused"]
    if paused_actions:
        for error in (audit.get("error"), job.get("error")):
            if isinstance(error, dict) and error.get("code") and error.get("message"):
                reasons.append({"code": error["code"], "message": error["message"],
                                "actions": paused_actions})
    return {
        "schema_version": 1,
        "snapshot_id": snapshot["snapshot_id"] if snapshot else None,
        "data_as_of": snapshot["data_as_of"] if snapshot else None,
        "generated_at": snapshot["generated_at"] if snapshot else None,
        "versions": versions,
        "rows": visible,
        "counts": {"snapshot_total": len(all_rows), "filtered_total": len(visible),
                   "brackets": brackets},
        "result_state": state,
        "audit_state": audit_state,
        "audit": audit,
        "job": job,
        "action_permissions": permissions,
        "blocked_reasons": reasons,
        "empty_kind": empty_kind,
        "selection": {"filters": chosen,
                      "funnel": snapshot.get("selection", {}).get("funnel", []) if snapshot else []},
        "response_revision": view["response_revision"],
    }
