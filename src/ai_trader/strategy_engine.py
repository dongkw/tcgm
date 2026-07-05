"""Minimal strategy engine for strategy_snapshot -> decision_result."""

from __future__ import annotations

from typing import Any

from .decision_utils import is_missing
from .snapshot_builder import BUY_EVALUATION, HOLDING_REVIEW


SELL_ACTIONS = {"REDUCE_HALF", "REDUCE_TO_WATCH", "CLEAR"}


def _rule(rule_id: str, group: str, name: str, status: str, severity: str, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "rule_group": group,
        "name": name,
        "status": status,
        "severity": severity,
        "message": message,
        "data_points": data or {},
    }


def _confidence(snapshot: dict[str, Any], warnings: list[dict[str, Any]]) -> str:
    score = snapshot.get("data_quality", {}).get("score") or 0
    data_warnings = snapshot.get("data_quality", {}).get("warning_missing_fields") or []
    if score >= 90 and not warnings and not data_warnings:
        return "HIGH"
    if score >= 75:
        return "MEDIUM"
    return "LOW"


def _trigger_prices(snapshot: dict[str, Any]) -> dict[str, Any]:
    tech = snapshot.get("technical") or {}
    return {
        "reduce_trigger_price": tech.get("ma20"),
        "clear_trigger_price": tech.get("low_20d"),
        "middle_trend_price": tech.get("ma60"),
        "resistance_price": tech.get("high_20d"),
        "stop_loss_price": tech.get("low_20d"),
    }


def _base_result(snapshot: dict[str, Any], final_action: str, reason: str, rules: list[dict[str, Any]]) -> dict[str, Any]:
    warnings = [r for r in rules if r["status"] in {"WARN", "UNKNOWN"}]
    blockers = [r for r in rules if r["status"] == "FAIL" and r["severity"] == "BLOCKER"]
    decision_id = snapshot["snapshot_id"].replace("ss_", "dr_", 1)
    return {
        "decision_id": decision_id,
        "snapshot_id": snapshot["snapshot_id"],
        "schema_version": "decision_result.v0.1",
        "symbol": snapshot.get("symbol"),
        "name": snapshot.get("name"),
        "task_type": snapshot.get("task_type"),
        "decision_time": snapshot.get("decision_time"),
        "time_context": snapshot.get("time_context") or {},
        "final_action": final_action,
        "confidence": _confidence(snapshot, warnings),
        "action_reason": reason,
        "rule_results": rules,
        "blocking_rules": blockers,
        "warning_rules": warnings,
        "trigger_prices": _trigger_prices(snapshot),
        "position_plan": _position_plan(snapshot, final_action),
        "invalidation_points": _invalidation_points(snapshot),
        "data_quality_summary": snapshot.get("data_quality") or {},
        "execution_constraints": _execution_constraints(snapshot, final_action),
        "human_review_required": True,
    }


def _position_plan(snapshot: dict[str, Any], final_action: str) -> dict[str, Any]:
    cash = snapshot.get("cash") or {}
    price = (snapshot.get("quote") or {}).get("price")
    low_20d = (snapshot.get("technical") or {}).get("low_20d")
    stop_loss_pct = None
    if price and low_20d and price > 0:
        stop_loss_pct = round(abs(price - low_20d) / price * 100, 2)

    return {
        "max_position_pct": None,
        "initial_position_pct": None,
        "suggested_cash_amount": None,
        "suggested_quantity": None,
        "risk_budget_pct": None,
        "stop_loss_pct": stop_loss_pct,
        "cash_enough": cash.get("provided") if final_action in {"BUY", "WATCH_SMALL"} else None,
        "position_downgrade_reasons": [],
    }


def _execution_constraints(snapshot: dict[str, Any], final_action: str) -> dict[str, Any]:
    position = snapshot.get("position") or {}
    available_quantity = position.get("available_quantity")
    t_plus_blocked = final_action in SELL_ACTIONS and (available_quantity is None or available_quantity == 0)
    return {
        "t_plus_one_blocked": t_plus_blocked,
        "available_quantity": available_quantity,
        "lot_size_valid": None,
        "price_limit_risk": None,
        "human_review_required": True,
    }


def _invalidation_points(snapshot: dict[str, Any]) -> dict[str, Any]:
    tech = snapshot.get("technical") or {}
    position = snapshot.get("position") or {}
    return {
        "fundamental": None,
        "technical": tech.get("low_20d"),
        "event": None,
        "original": position.get("invalidation_point"),
    }


def run_strategy(snapshot: dict[str, Any]) -> dict[str, Any]:
    task_type = snapshot.get("task_type")
    rules: list[dict[str, Any]] = []

    dq = snapshot.get("data_quality") or {}
    blocking_missing = dq.get("blocking_missing_fields") or []
    if blocking_missing or (dq.get("score") or 0) < 60:
        rules.append(_rule("R0-1", "R0_DATA_QUALITY", "data quality gate", "FAIL", "BLOCKER", "blocking data gaps", {"missing": blocking_missing, "score": dq.get("score")}))
        return _base_result(snapshot, "DATA_BLOCKED", "blocking data gaps prevent a full decision", rules)
    rules.append(_rule("R0-1", "R0_DATA_QUALITY", "data quality gate", "PASS", "BLOCKER", "required data is usable", {"score": dq.get("score")}))

    if task_type == HOLDING_REVIEW:
        return _holding_review(snapshot, rules)
    if task_type == BUY_EVALUATION:
        return _buy_evaluation(snapshot, rules)

    rules.append(_rule("R0-2", "R0_DATA_QUALITY", "task type", "FAIL", "BLOCKER", f"unsupported task_type: {task_type}"))
    return _base_result(snapshot, "DATA_BLOCKED", "unsupported task type", rules)


def _holding_review(snapshot: dict[str, Any], rules: list[dict[str, Any]]) -> dict[str, Any]:
    position = snapshot.get("position") or {}
    quote = snapshot.get("quote") or {}
    tech = snapshot.get("technical") or {}
    price = quote.get("price")
    ma20 = tech.get("ma20")
    ma60 = tech.get("ma60")
    low_20d = tech.get("low_20d")
    change_20d = tech.get("change_20d_pct")

    missing_position = [
        key
        for key in ["avg_cost", "position_pct", "buy_logic", "invalidation_point"]
        if is_missing(position.get(key))
    ]
    if missing_position:
        rules.append(_rule("R0-5", "R0_DATA_QUALITY", "holding context", "UNKNOWN", "MAJOR", "holding context is incomplete", {"missing": missing_position}))
        return _base_result(snapshot, "PRE_EVALUATION", "holding cost, position, buy logic, or invalidation point is missing", rules)
    rules.append(_rule("R0-5", "R0_DATA_QUALITY", "holding context", "PASS", "MAJOR", "holding context is complete"))

    final_action = "HOLD"
    reason = "no sell trigger is active"

    if price is not None and low_20d is not None and price < low_20d:
        final_action = "CLEAR"
        reason = "price is below the recent 20-day low"
        rules.append(_rule("R9-5", "R9_HOLDING_SELL", "break 20-day low", "FAIL", "MAJOR", reason, {"price": price, "low_20d": low_20d}))
    elif price is not None and ma20 is not None and price < ma20:
        final_action = "REDUCE_HALF"
        reason = "price is below MA20"
        rules.append(_rule("R9-4", "R9_HOLDING_SELL", "below MA20", "FAIL", "MAJOR", reason, {"price": price, "ma20": ma20}))
    else:
        rules.append(_rule("R9-4", "R9_HOLDING_SELL", "below MA20", "PASS", "MAJOR", "price is not below MA20", {"price": price, "ma20": ma20}))

    if final_action == "HOLD" and price is not None and ma60 is not None and price < ma60:
        final_action = "REDUCE_HALF"
        reason = "price is below MA60"
        rules.append(_rule("R9-6", "R9_HOLDING_SELL", "below MA60", "FAIL", "MAJOR", reason, {"price": price, "ma60": ma60}))
    elif price is not None and ma60 is not None:
        rules.append(_rule("R9-6", "R9_HOLDING_SELL", "below MA60", "PASS", "MAJOR", "price is not below MA60", {"price": price, "ma60": ma60}))

    if final_action == "HOLD" and change_20d is not None and change_20d > 80:
        final_action = "REDUCE_HALF"
        reason = "20-day gain is above 80%; active profit-taking is suggested"
        rules.append(_rule("R9-7", "R9_HOLDING_SELL", "sharp short-term gain", "WARN", "MAJOR", reason, {"change_20d_pct": change_20d}))

    available_quantity = position.get("available_quantity")
    if final_action in SELL_ACTIONS and (available_quantity is None or available_quantity == 0):
        rules.append(_rule("R9-8", "R9_HOLDING_SELL", "T+1 available quantity", "FAIL", "BLOCKER", "sell trigger exists but no available quantity", {"available_quantity": available_quantity}))
        return _base_result(snapshot, "NO_SELL_T_PLUS", "sell trigger exists but there is no available quantity", rules)

    return _base_result(snapshot, final_action, reason, rules)


def _buy_evaluation(snapshot: dict[str, Any], rules: list[dict[str, Any]]) -> dict[str, Any]:
    flags = snapshot.get("flags") or {}
    tech = snapshot.get("technical") or {}
    valuation = snapshot.get("valuation") or {}
    events = snapshot.get("events") or {}

    if flags.get("a2_deducted_profit_negative_2y"):
        rules.append(_rule("R2-3", "R2_HARD_BLOCKS", "deducted profit negative two years", "FAIL", "BLOCKER", "deducted net profit is negative for two years"))
        return _base_result(snapshot, "DO_NOT_BUY", "hard block: deducted net profit is negative for two years", rules)

    if flags.get("chase_high_1m_over_80_pct"):
        rules.append(_rule("R2-4", "R2_HARD_BLOCKS", "one-month gain over 80%", "FAIL", "BLOCKER", "one-month gain is over 80%"))
        return _base_result(snapshot, "DO_NOT_BUY", "hard block: one-month gain is over 80%", rules)

    if events.get("risk_flags", {}).get("regulatory"):
        rules.append(_rule("R5-1", "R5_EVENT_RISK", "regulatory risk", "WARN", "MAJOR", "regulatory announcement title exists; source text needs review"))

    if flags.get("chase_high_1m_50_to_80_pct"):
        rules.append(_rule("R2-6", "R2_HARD_BLOCKS", "one-month gain 50%-80%", "WARN", "MAJOR", "short-term gain is high; highest action is WATCH_SMALL"))
        return _base_result(snapshot, "WATCH_SMALL", "short-term gain is high; only small watch position is allowed", rules)

    above_ma20 = tech.get("above_ma20")
    above_ma60 = tech.get("above_ma60")
    ma20_up = tech.get("ma20_slope_up")
    ma60_up = tech.get("ma60_slope_up")
    pe_pct = valuation.get("pe_5y_percentile")
    pb_pct = valuation.get("pb_5y_percentile")

    if above_ma20 and above_ma60 and ma20_up and ma60_up:
        rules.append(_rule("R6-1", "R6_TECHNICAL", "MA20/MA60 trend", "PASS", "MAJOR", "price is above rising MA20 and MA60"))
        action = "WATCH_SMALL"
        reason = "trend is valid, but first-round engine does not issue strong BUY without full logic, cash, and valuation assumptions"
    else:
        rules.append(_rule("R6-1", "R6_TECHNICAL", "MA20/MA60 trend", "WARN", "MAJOR", "trend confirmation is incomplete"))
        action = "WAIT"
        reason = "trend confirmation is incomplete"

    if pe_pct is None or pb_pct is None:
        rules.append(_rule("R4-1", "R4_VALUATION", "valuation percentiles", "UNKNOWN", "MAJOR", "valuation percentile is missing; action cannot be upgraded"))
        action = "WAIT" if action == "BUY" else action

    return _base_result(snapshot, action, reason, rules)
