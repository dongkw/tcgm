"""Risk checks for portfolio planning.

This module is intentionally deterministic and file-ledger friendly. It does
not decide whether a stock is good; it only decides whether a decision_result
can enter a buy plan and how much cash it may use.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .portfolio import compact_now, now_iso


BUY_ACTIONS = {"BUY", "WATCH_SMALL"}
RECORD_ONLY_ACTIONS = {"WAIT", "HOLD", "PRE_EVALUATION", "NO_SELL_T_PLUS"}
REJECT_ACTIONS = {"DO_NOT_BUY"}
BLOCK_ACTIONS = {"DATA_BLOCKED"}


DEFAULT_RISK_CONFIG = {
    "max_single_position_pct": 30.0,
    "max_equity_position_pct": 80.0,
    "cash_reserve_pct": 20.0,
    "max_daily_buy_amount": 30000.0,
    "allow_low_confidence_buy": False,
    "allow_non_trading_plan": True,
}


def merge_risk_config(account: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(DEFAULT_RISK_CONFIG)
    merged.update(config or {})
    for key in ["cash_reserve_pct", "max_single_position_pct", "max_daily_buy_amount"]:
        if account.get(key) is not None:
            merged[key] = float(account[key])
    return merged


def reference_quote(decision: dict[str, Any], snapshot: dict[str, Any] | None, output_root: Path) -> dict[str, Any]:
    """Resolve the reference price with the same priority as the docs."""

    symbol = decision.get("symbol")
    snapshot_time_context = (snapshot or {}).get("time_context") or {}
    decision_trade_date = (
        (decision.get("time_context") or {}).get("trade_date")
        or snapshot_time_context.get("trade_date")
        or (snapshot or {}).get("trade_date")
    )

    if snapshot:
        quote = snapshot.get("quote") or {}
        if quote.get("price"):
            return {
                "price": float(quote["price"]),
                "trade_date": snapshot.get("trade_date") or decision_trade_date,
                "quote_time": snapshot.get("decision_time"),
                "quote_source": "strategy_snapshot.quote.price",
            }

    if decision.get("reference_price"):
        return {
            "price": float(decision["reference_price"]),
            "trade_date": decision_trade_date,
            "quote_time": decision.get("decision_time"),
            "quote_source": "decision_result.reference_price",
        }

    decision_quote = decision.get("quote") or {}
    if decision_quote.get("price"):
        return {
            "price": float(decision_quote["price"]),
            "trade_date": decision_quote.get("trade_date") or decision_trade_date,
            "quote_time": decision.get("decision_time"),
            "quote_source": "decision_result.quote.price",
        }

    if symbol:
        stock_path = output_root / "stock_json" / f"stock_data_{symbol}.json"
        if stock_path.exists():
            import json

            stock_data = json.loads(stock_path.read_text(encoding="utf-8"))
            quote = stock_data.get("quote") or {}
            if quote.get("price"):
                return {
                    "price": float(quote["price"]),
                    "trade_date": quote.get("trade_date"),
                    "quote_time": None,
                    "quote_source": "stock_json.quote.price",
                }

    return {
        "price": None,
        "trade_date": decision_trade_date,
        "quote_time": None,
        "quote_source": None,
        "reject_reason": "PRICE_MISSING",
    }


def check_decision_risk(
    decision: dict[str, Any],
    snapshot: dict[str, Any] | None,
    account: dict[str, Any],
    account_positions: dict[str, Any],
    trade_history: list[dict[str, Any]],
    output_root: Path,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    risk_config = merge_risk_config(account, config)
    rules: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    original_action = decision.get("final_action")
    allowed_action = original_action
    risk_status = "PASS"
    risk_level = "LOW"
    execution_allowed = True
    max_cash_amount: float | None = None
    max_quantity: int | None = None

    symbol = decision.get("symbol")
    time_context = _effective_time_context(decision, snapshot)
    quote = reference_quote(decision, snapshot, output_root)
    trade_date = time_context.get("trade_date") or (snapshot or {}).get("trade_date") or quote.get("trade_date")

    def block(rule_id: str, reason: str, level: str = "HIGH") -> dict[str, Any]:
        return _result(
            decision,
            account,
            quote,
            original_action=original_action,
            allowed_action="BLOCKED",
            risk_status="BLOCK",
            risk_level=level,
            max_cash_amount=0.0,
            max_quantity=0,
            blocking_rules=rules + [_rule(rule_id, reason, "BLOCK")],
            warning_rules=warnings,
            execution_allowed=False,
        )

    if not symbol:
        return block("R0_SYMBOL", "SYMBOL_MISSING")
    if not trade_date:
        return block("R0_TRADE_DATE", "TRADE_DATE_MISSING")

    if any(trade.get("decision_id") == decision.get("decision_id") for trade in trade_history):
        return block("R6_DUPLICATE", "DUPLICATE_DECISION_EXECUTION")

    if original_action in BLOCK_ACTIONS:
        return block("R2_ACTION", "DATA_BLOCKED")

    if original_action in REJECT_ACTIONS:
        rules.append(_rule("R2_ACTION", "ACTION_REJECTED", "REJECT"))
        return _result(
            decision,
            account,
            quote,
            original_action=original_action,
            allowed_action="REJECTED",
            risk_status="REJECT",
            risk_level="MEDIUM",
            max_cash_amount=0.0,
            max_quantity=0,
            blocking_rules=rules,
            warning_rules=warnings,
            execution_allowed=False,
        )

    if decision.get("task_type") != "BUY_EVALUATION" or original_action in RECORD_ONLY_ACTIONS:
        warnings.append(_rule("R2_RECORD_ONLY", "ACTION_RECORD_ONLY", "WARN"))
        return _result(
            decision,
            account,
            quote,
            original_action=original_action,
            allowed_action="RECORD_ONLY",
            risk_status="PASS",
            risk_level="LOW",
            max_cash_amount=0.0,
            max_quantity=0,
            blocking_rules=[],
            warning_rules=warnings,
            execution_allowed=False,
        )

    if original_action not in BUY_ACTIONS:
        rules.append(_rule("R2_ACTION", "ACTION_NOT_TRADABLE", "REJECT"))
        return _result(
            decision,
            account,
            quote,
            original_action=original_action,
            allowed_action="REJECTED",
            risk_status="REJECT",
            risk_level="MEDIUM",
            max_cash_amount=0.0,
            max_quantity=0,
            blocking_rules=rules,
            warning_rules=warnings,
            execution_allowed=False,
        )

    price = quote.get("price")
    if not price or float(price) <= 0:
        return block("R0_PRICE", quote.get("reject_reason") or "PRICE_MISSING")
    if quote.get("trade_date") and quote.get("trade_date") != trade_date:
        return block("R0_PRICE_TIME", "PRICE_TIME_MISMATCH")

    if not time_context.get("is_trading_day") or time_context.get("session_name") == "NON_TRADING":
        warnings.append(_rule("R0_TIME", "NON_TRADING_DAY", "WARN"))
        return _result(
            decision,
            account,
            quote,
            original_action=original_action,
            allowed_action="RECORD_ONLY_PLAN",
            risk_status="WARN",
            risk_level="MEDIUM",
            max_cash_amount=0.0,
            max_quantity=0,
            blocking_rules=[],
            warning_rules=warnings,
            execution_allowed=False,
        )

    if account.get("last_rollover_trade_date") != trade_date:
        return block("R6_ROLLOVER", "DAILY_ROLLOVER_MISSING")

    data_quality = decision.get("data_quality_summary") or {}
    if data_quality.get("level") == "BLOCKED":
        return block("R0_DATA", "DATA_BLOCKED")

    confidence = decision.get("confidence")
    if confidence == "LOW" and not risk_config.get("allow_low_confidence_buy"):
        if original_action == "BUY":
            allowed_action = "WATCH_SMALL"
            risk_status = "DOWNGRADE"
            risk_level = "MEDIUM"
            warnings.append(_rule("R2_CONFIDENCE", "LOW_CONFIDENCE_DOWNGRADE_TO_WATCH_SMALL", "WARN"))
        else:
            warnings.append(_rule("R2_CONFIDENCE", "LOW_CONFIDENCE_RECORD_ONLY", "WARN"))
            return _result(
                decision,
                account,
                quote,
                original_action=original_action,
                allowed_action="RECORD_ONLY",
                risk_status="DOWNGRADE",
                risk_level="MEDIUM",
                max_cash_amount=0.0,
                max_quantity=0,
                blocking_rules=[],
                warning_rules=warnings,
                execution_allowed=False,
            )

    available_cash = float(account.get("available_cash") or 0.0)
    total_assets = float(account.get("total_assets") or 0.0)
    cash_reserve = total_assets * float(risk_config["cash_reserve_pct"]) / 100
    usable_cash = max(available_cash - cash_reserve, 0.0)
    daily_budget_left = max(float(risk_config["max_daily_buy_amount"]) - float(account.get("today_buy_used") or 0.0), 0.0)
    buy_budget = min(usable_cash, daily_budget_left)

    if buy_budget <= 0:
        rules.append(_rule("R3_BUDGET", "NO_BUY_BUDGET", "REJECT"))
        return _result(
            decision,
            account,
            quote,
            original_action=original_action,
            allowed_action="REJECTED",
            risk_status="REJECT",
            risk_level="MEDIUM",
            max_cash_amount=0.0,
            max_quantity=0,
            blocking_rules=rules,
            warning_rules=warnings,
            execution_allowed=False,
        )

    current_position = account_positions.get(symbol) or {}
    current_market_value = float(current_position.get("market_value") or 0.0)
    if not current_market_value:
        current_market_value = float(current_position.get("total_quantity") or 0) * float(current_position.get("market_price") or 0.0)
    single_limit = total_assets * float(risk_config["max_single_position_pct"]) / 100
    single_remaining = max(single_limit - current_market_value, 0.0)
    max_cash_amount = min(buy_budget, single_remaining)
    max_quantity = int(max_cash_amount // (float(price) * 100) * 100)

    if max_quantity < 100:
        rules.append(_rule("R4_SINGLE_POSITION", "CASH_NOT_ENOUGH_FOR_ONE_LOT", "REJECT"))
        return _result(
            decision,
            account,
            quote,
            original_action=original_action,
            allowed_action="REJECTED",
            risk_status="REJECT",
            risk_level="MEDIUM",
            max_cash_amount=max_cash_amount,
            max_quantity=max_quantity,
            blocking_rules=rules,
            warning_rules=warnings,
            execution_allowed=False,
        )

    return _result(
        decision,
        account,
        quote,
        original_action=original_action,
        allowed_action=allowed_action,
        risk_status=risk_status,
        risk_level=risk_level,
        max_cash_amount=round(max_cash_amount, 2),
        max_quantity=max_quantity,
        blocking_rules=rules,
        warning_rules=warnings,
        execution_allowed=execution_allowed,
    )


def _rule(rule_id: str, reason: str, status: str) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "reason": reason,
        "status": status,
    }


def _result(
    decision: dict[str, Any],
    account: dict[str, Any],
    quote: dict[str, Any],
    *,
    original_action: str | None,
    allowed_action: str | None,
    risk_status: str,
    risk_level: str,
    max_cash_amount: float | None,
    max_quantity: int | None,
    blocking_rules: list[dict[str, Any]],
    warning_rules: list[dict[str, Any]],
    execution_allowed: bool,
) -> dict[str, Any]:
    return {
        "risk_check_id": f"risk_{compact_now()}",
        "account_id": account.get("account_id"),
        "decision_id": decision.get("decision_id"),
        "snapshot_id": decision.get("snapshot_id"),
        "symbol": decision.get("symbol"),
        "name": decision.get("name"),
        "trade_date": _effective_time_context(decision, None).get("trade_date") or quote.get("trade_date"),
        "original_action": original_action,
        "allowed_action": allowed_action,
        "risk_status": risk_status,
        "risk_level": risk_level,
        "max_cash_amount": max_cash_amount,
        "max_quantity": max_quantity,
        "reference_price": quote.get("price"),
        "quote_source": quote.get("quote_source"),
        "blocking_rules": blocking_rules,
        "warning_rules": warning_rules,
        "human_review_required": True,
        "execution_allowed": execution_allowed,
        "created_at": now_iso(),
    }


def _effective_time_context(decision: dict[str, Any], snapshot: dict[str, Any] | None) -> dict[str, Any]:
    time_context = dict((snapshot or {}).get("time_context") or {})
    time_context.update(decision.get("time_context") or {})
    if not time_context.get("trade_date"):
        time_context["trade_date"] = decision.get("trade_date") or (snapshot or {}).get("trade_date")
    return time_context
