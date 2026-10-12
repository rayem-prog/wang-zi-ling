"""Conservative action eligibility from a single persisted result view."""

from __future__ import annotations


OPERATIONS = ("view_candidates", "create_buy", "add_position", "export_orders")
TRADE_OPERATIONS = ("create_buy", "add_position", "export_orders")


def evaluate_action_permissions(result_state: str, *, has_rows: bool,
                                allow_new_buy: bool | None,
                                revocation: dict | None = None) -> tuple[dict, list[dict]]:
    permissions = {action: "paused" for action in OPERATIONS}
    reasons = []

    def reason(code, message, actions):
        reasons.append({"code": code, "message": message, "actions": list(actions)})

    if result_state == "revoked":
        permissions = {action: "forbidden" for action in OPERATIONS}
        reason((revocation or {}).get("code", "revoked"),
               (revocation or {}).get("message", "This result was revoked."), OPERATIONS)
        return permissions, reasons
    if result_state == "none":
        reason("no_snapshot", "No result has been published.", OPERATIONS)
        return permissions, reasons

    permissions["view_candidates"] = "allowed"
    if allow_new_buy is False:
        permissions["create_buy"] = "forbidden"
        permissions["add_position"] = "forbidden"
        reason("new_buy_forbidden", "Risk policy forbids new buys.", ("create_buy", "add_position"))
    if result_state != "current":
        reason("historical_result", "Historical results are for review only.",
               tuple(action for action in TRADE_OPERATIONS if permissions[action] == "paused"))
    elif not has_rows:
        reason("no_candidates", "No candidates are available.",
               tuple(action for action in TRADE_OPERATIONS if permissions[action] == "paused"))
    else:
        reason("operation_recheck_required", "Current account and risk checks are required.",
               tuple(action for action in TRADE_OPERATIONS if permissions[action] == "paused"))
    return permissions, reasons
