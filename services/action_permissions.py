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
               (revocation or {}).get("message", "该结果已撤销。"), OPERATIONS)
        return permissions, reasons
    if result_state == "none":
        reason("no_snapshot", "尚无已发布结果。", OPERATIONS)
        return permissions, reasons

    permissions["view_candidates"] = "allowed"
    if allow_new_buy is False:
        permissions["create_buy"] = "forbidden"
        permissions["add_position"] = "forbidden"
        reason("new_buy_forbidden", "当前风控禁止新买入和加仓。", ("create_buy", "add_position"))
    if result_state != "current":
        reason("historical_result", "历史结果仅供回看，不能据此操作。",
               tuple(action for action in TRADE_OPERATIONS if permissions[action] == "paused"))
    elif not has_rows:
        reason("no_candidates", "当前没有可操作的候选标的。",
               tuple(action for action in TRADE_OPERATIONS if permissions[action] == "paused"))
    else:
        reason("operation_recheck_required", "操作前需重新核验账户和风控状态。",
               tuple(action for action in TRADE_OPERATIONS if permissions[action] == "paused"))
    return permissions, reasons
