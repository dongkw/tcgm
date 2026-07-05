"""Portfolio construction for third-round buy planning."""

from __future__ import annotations

import math
from typing import Any

from .cost_model import money
from .portfolio import compact_now, now_iso


ELIGIBLE_RISK_STATUS = {"PASS", "WARN", "DOWNGRADE"}
ELIGIBLE_ACTIONS = {"BUY", "WATCH_SMALL"}


def build_allocation(
    candidates: list[dict[str, Any]],
    account: dict[str, Any],
    account_positions: dict[str, Any],
    *,
    trade_date: str,
    strategy_version: str,
    default_watch_cash: float = 5000.0,
    max_candidates: int | None = None,
) -> dict[str, Any]:
    allocation_id = f"alloc_{compact_now()}"
    total_assets = float(account.get("total_assets") or 0.0)
    available_cash = float(account.get("available_cash") or 0.0)
    cash_reserve_pct = float(account.get("cash_reserve_pct") or 20.0)
    max_daily_buy_amount = float(account.get("max_daily_buy_amount") or 0.0)
    today_buy_used = float(account.get("today_buy_used") or 0.0)
    cash_reserved = money(total_assets * cash_reserve_pct / 100)
    usable_cash = max(available_cash - cash_reserved, 0.0)
    daily_budget_left = max_daily_buy_amount - today_buy_used if max_daily_buy_amount > 0 else usable_cash
    buy_budget = money(max(min(usable_cash, daily_budget_left), 0.0))

    scored = []
    for candidate in candidates:
        risk_check = candidate["risk_check"]
        decision = candidate["decision"]
        score = score_candidate(decision, risk_check, account_positions)
        candidate = dict(candidate)
        candidate["score"] = score["total_score"]
        candidate["score_breakdown"] = score
        scored.append(candidate)

    scored.sort(
        key=lambda c: (
            c["score"],
            (c["decision"].get("data_quality_summary") or {}).get("score") or 0,
            c["decision"].get("decision_time") or "",
        ),
        reverse=True,
    )
    if max_candidates:
        scored = scored[:max_candidates]

    remaining_budget = buy_budget
    order_intents: list[dict[str, Any]] = []
    ready_count = 0
    rejected_count = 0
    deferred_count = 0
    record_only_count = 0
    planned_buy_amount = 0.0

    for rank, candidate in enumerate(scored, start=1):
        decision = candidate["decision"]
        risk_check = candidate["risk_check"]
        status, reason = _candidate_status(risk_check)
        planned_cash = 0.0
        planned_quantity = 0

        if status == "READY_FOR_CONFIRM":
            target_cash = _target_cash(decision, risk_check, total_assets, default_watch_cash)
            reference_price = float(risk_check.get("reference_price") or 0.0)
            max_cash_amount = float(risk_check.get("max_cash_amount") or 0.0)
            planned_cash = min(target_cash, max_cash_amount, remaining_budget)
            planned_quantity = _normalize_buy_quantity(planned_cash / reference_price) if reference_price > 0 else 0
            if planned_quantity < 100:
                status = "DEFERRED"
                reason = "CASH_NOT_ENOUGH_FOR_ONE_LOT"
                planned_cash = 0.0
                planned_quantity = 0
            else:
                planned_cash = money(planned_quantity * reference_price)
                remaining_budget = money(remaining_budget - planned_cash)
                planned_buy_amount = money(planned_buy_amount + planned_cash)
                ready_count += 1

        if status == "REJECTED":
            rejected_count += 1
        elif status == "DEFERRED":
            deferred_count += 1
        elif status == "RECORD_ONLY":
            record_only_count += 1

        order_intents.append(
            {
                "intent_id": f"oi_{compact_now()}",
                "allocation_id": allocation_id,
                "account_id": account.get("account_id"),
                "decision_id": decision.get("decision_id"),
                "snapshot_id": decision.get("snapshot_id"),
                "symbol": decision.get("symbol"),
                "name": decision.get("name"),
                "side": "BUY" if status == "READY_FOR_CONFIRM" else None,
                "rank": rank,
                "score": candidate["score"],
                "score_breakdown": candidate["score_breakdown"],
                "planned_cash_amount": planned_cash,
                "planned_quantity": planned_quantity,
                "reference_price": risk_check.get("reference_price"),
                "reason": reason,
                "status": status,
                "created_at": now_iso(),
            }
        )

    if buy_budget <= 0:
        plan_status = "NO_BUY_BUDGET"
    elif ready_count == 0:
        plan_status = "NO_ELIGIBLE_CANDIDATE"
    else:
        plan_status = "READY_FOR_CONFIRM"

    allocation_plan = {
        "allocation_id": allocation_id,
        "account_id": account.get("account_id"),
        "trade_date": trade_date,
        "strategy_version": strategy_version,
        "cash_before": money(available_cash),
        "cash_reserved": cash_reserved,
        "buy_budget": buy_budget,
        "planned_buy_amount": planned_buy_amount,
        "planned_position_count": ready_count,
        "candidate_count": len(scored),
        "rejected_count": rejected_count,
        "deferred_count": deferred_count,
        "record_only_count": record_only_count,
        "status": plan_status,
        "created_at": now_iso(),
    }
    return {
        "allocation_plan": allocation_plan,
        "order_intents": order_intents,
    }


def score_candidate(decision: dict[str, Any], risk_check: dict[str, Any], account_positions: dict[str, Any]) -> dict[str, Any]:
    allowed_action = risk_check.get("allowed_action")
    final_action = allowed_action if allowed_action in ELIGIBLE_ACTIONS else decision.get("final_action")

    signal_score = 40 if final_action == "BUY" else 25 if final_action == "WATCH_SMALL" else 0
    confidence = decision.get("confidence")
    confidence_score = {"HIGH": 15, "MEDIUM": 8, "LOW": 0}.get(confidence, 0)
    data_quality_raw = (decision.get("data_quality_summary") or {}).get("score") or 0
    data_quality_score = round(float(data_quality_raw) * 0.2, 2)
    portfolio_fit_score = _portfolio_fit_score(decision, account_positions)
    user_priority_score = 0
    penalty_score = _penalty_score(decision, risk_check, data_quality_raw)
    total_score = round(
        signal_score
        + confidence_score
        + data_quality_score
        + portfolio_fit_score
        + user_priority_score
        - penalty_score,
        2,
    )
    return {
        "total_score": total_score,
        "signal_score": signal_score,
        "confidence_score": confidence_score,
        "data_quality_score": data_quality_score,
        "portfolio_fit_score": portfolio_fit_score,
        "user_priority_score": user_priority_score,
        "penalty_score": penalty_score,
    }


def _portfolio_fit_score(decision: dict[str, Any], account_positions: dict[str, Any]) -> float:
    position = account_positions.get(decision.get("symbol")) or {}
    if not position or position.get("position_status") == "CLOSED":
        return 5.0
    position_pct = float(position.get("position_pct") or 0.0)
    if position_pct > 20:
        return -10.0
    return 0.0


def _penalty_score(decision: dict[str, Any], risk_check: dict[str, Any], data_quality_raw: float) -> float:
    penalty = 0.0
    if risk_check.get("risk_status") == "WARN":
        penalty += 5
    if risk_check.get("risk_status") == "DOWNGRADE":
        penalty += 10
    if decision.get("confidence") == "LOW":
        penalty += 10
    if float(data_quality_raw or 0.0) < 75:
        penalty += 10
    return penalty


def _candidate_status(risk_check: dict[str, Any]) -> tuple[str, str]:
    risk_status = risk_check.get("risk_status")
    allowed_action = risk_check.get("allowed_action")
    if risk_status in {"BLOCK", "REJECT"}:
        return "REJECTED", _first_reason(risk_check)
    if allowed_action == "RECORD_ONLY_PLAN" or not risk_check.get("execution_allowed"):
        return "RECORD_ONLY", _first_reason(risk_check) or "RECORD_ONLY"
    if risk_status not in ELIGIBLE_RISK_STATUS or allowed_action not in ELIGIBLE_ACTIONS:
        return "RECORD_ONLY", _first_reason(risk_check) or "ACTION_NOT_TRADABLE"
    return "READY_FOR_CONFIRM", "eligible by risk check and allocation rules"


def _target_cash(
    decision: dict[str, Any],
    risk_check: dict[str, Any],
    total_assets: float,
    default_watch_cash: float,
) -> float:
    allowed_action = risk_check.get("allowed_action")
    if allowed_action == "BUY":
        target = total_assets * 0.10
    else:
        target = min(total_assets * 0.05, default_watch_cash)
    if risk_check.get("risk_status") == "DOWNGRADE":
        target = min(target, total_assets * 0.03)
    suggested_cash = (decision.get("position_plan") or {}).get("suggested_cash_amount")
    if suggested_cash:
        target = min(target, float(suggested_cash))
    return max(target, 0.0)


def _normalize_buy_quantity(raw_quantity: float) -> int:
    return int(math.floor(raw_quantity / 100) * 100)


def _first_reason(risk_check: dict[str, Any]) -> str | None:
    for key in ["blocking_rules", "warning_rules"]:
        rules = risk_check.get(key) or []
        if rules:
            return rules[0].get("reason")
    return None
