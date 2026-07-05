"""Build strategy snapshots from the current stock JSON files."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .decision_utils import first_non_missing, get_in, is_missing


BUY_EVALUATION = "BUY_EVALUATION"
HOLDING_REVIEW = "HOLDING_REVIEW"


COMMON_REQUIRED = {
    "symbol": "quote.code",
    "name": "quote.name",
    "exchange": "quote.market",
    "trade_date": "quote.trade_date",
    "price": "quote.price",
    "ma20": "technical.ma20",
    "ma60": "technical.ma60",
    "high_20d": "technical.high_20d",
    "low_20d": "technical.low_20d",
}

BUY_REQUIRED = {
    "latest_notice_date": "financial.latest.notice_date",
    "latest_revenue_yuan": "financial.latest.revenue_yuan",
    "latest_parent_net_profit_yuan": "financial.latest.parent_net_profit_yuan",
    "latest_deducted_net_profit_yuan": "financial.latest.deducted_net_profit_yuan",
    "latest_operating_cashflow_yuan": "financial.latest.operating_cashflow_yuan",
}


def _build_position(user_context: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "avg_cost",
        "position_pct",
        "total_quantity",
        "available_quantity",
        "buy_logic",
        "invalidation_point",
        "holding_period",
    ]
    position = {key: user_context.get(key) for key in keys}
    position["provided"] = any(not is_missing(position[key]) for key in keys)
    return position


def _build_cash(user_context: dict[str, Any]) -> dict[str, Any]:
    keys = ["available_cash", "total_assets", "cash_reserve_pct"]
    cash = {key: user_context.get(key) for key in keys}
    cash["provided"] = any(not is_missing(cash[key]) for key in keys)
    return cash


def _quality_result(raw_data: dict[str, Any], task_type: str, position: dict[str, Any]) -> dict[str, Any]:
    blocking: list[str] = []
    warnings: list[str] = []
    replay_lite = raw_data.get("replay_mode") == "REPLAY_LITE"

    for label, path in COMMON_REQUIRED.items():
        if is_missing(get_in(raw_data, path)):
            blocking.append(label)

    if task_type == BUY_EVALUATION and replay_lite:
        for label, path in BUY_REQUIRED.items():
            if is_missing(get_in(raw_data, path)):
                warnings.append(f"{label}:replay_lite_disabled")
    elif task_type == BUY_EVALUATION:
        for label, path in BUY_REQUIRED.items():
            if is_missing(get_in(raw_data, path)):
                blocking.append(label)

    if task_type == HOLDING_REVIEW:
        for label in ["avg_cost", "position_pct", "buy_logic", "invalidation_point"]:
            if is_missing(position.get(label)):
                warnings.append(label)
        if is_missing(position.get("available_quantity")):
            warnings.append("available_quantity")

    for gap in raw_data.get("data_gaps_for_engine") or []:
        warnings.append(str(gap))

    score = max(0, 100 - len(blocking) * 20 - min(len(warnings), 10) * 3)
    if blocking:
        level = "BLOCKED"
    elif score >= 90:
        level = "GOOD"
    elif score >= 75:
        level = "USABLE"
    elif score >= 60:
        level = "WEAK"
    else:
        level = "BLOCKED"

    return {
        "score": score,
        "level": level,
        "completeness": max(0, 100 - len(blocking) * 20),
        "freshness": 85,
        "trust": 80,
        "consistency": 90,
        "timeliness": 85,
        "blocking_missing_fields": blocking,
        "warning_missing_fields": warnings,
        "time_warnings": [],
    }


def build_strategy_snapshot(
    raw_data: dict[str, Any],
    time_context: dict[str, Any],
    task_type: str,
    user_context: dict[str, Any] | None = None,
    source_file: str | None = None,
) -> dict[str, Any]:
    user_context = user_context or {}
    position = _build_position(user_context)
    cash = _build_cash(user_context)
    data_quality = _quality_result(raw_data, task_type, position)
    data_quality["time_warnings"] = list(time_context.get("time_warnings") or [])

    symbol = get_in(raw_data, "quote.code")
    decision_dt = datetime.fromisoformat(time_context["decision_time"])
    decision_compact = decision_dt.strftime("%Y%m%dT%H%M%S%f")
    snapshot_id = f"ss_{symbol}_{task_type.lower()}_{decision_compact}"

    quote = raw_data.get("quote") or {}
    technical = raw_data.get("technical") or {}
    valuation = raw_data.get("valuation") or {}

    return {
        "snapshot_id": snapshot_id,
        "source_file": source_file,
        "schema_version": "strategy_snapshot.v0.1",
        "task_type": task_type,
        "asset_type": "A_STOCK",
        "symbol": symbol,
        "name": get_in(raw_data, "quote.name"),
        "exchange": get_in(raw_data, "quote.market"),
        "decision_time": time_context["decision_time"],
        "trade_date": time_context.get("trade_date"),
        "source_quote_trade_date": get_in(raw_data, "quote.trade_date"),
        "time_context": time_context,
        "quote": {
            "price": quote.get("price"),
            "trade_date": get_in(raw_data, "quote.trade_date"),
            "pct_change": quote.get("pct_change"),
            "market_cap_yuan": quote.get("market_cap_yuan"),
            "pe_ttm": quote.get("pe_ttm"),
            "pb": quote.get("pb"),
        },
        "technical": {
            "ma20": technical.get("ma20"),
            "ma60": technical.get("ma60"),
            "above_ma20": technical.get("above_ma20"),
            "above_ma60": technical.get("above_ma60"),
            "ma20_slope_up": technical.get("ma20_slope_up"),
            "ma60_slope_up": technical.get("ma60_slope_up"),
            "high_20d": technical.get("high_20d"),
            "low_20d": technical.get("low_20d"),
            "change_20d_pct": technical.get("change_20d_pct"),
            "change_60d_pct": technical.get("change_60d_pct"),
            "atr14_pct": technical.get("atr14_pct"),
        },
        "valuation": {
            "pe_ttm": first_non_missing(valuation.get("pe_ttm"), quote.get("pe_ttm")),
            "pb": first_non_missing(valuation.get("pb"), quote.get("pb")),
            "pe_5y_percentile": valuation.get("pe_5y_percentile"),
            "pb_5y_percentile": valuation.get("pb_5y_percentile"),
            "note": valuation.get("note"),
        },
        "financial": raw_data.get("financial") or {},
        "events": {
            "announcements": raw_data.get("announcements") or {},
            "risk_flags": {
                "reduction": bool(get_in(raw_data, "announcements.减持", [])),
                "regulatory": bool(get_in(raw_data, "announcements.监管", [])),
                "buyback": bool(get_in(raw_data, "announcements.回购", [])),
            },
        },
        "ownership": raw_data.get("ownership") or {},
        "position": position,
        "cash": cash,
        "flags": raw_data.get("engine_flags") or {},
        "data_quality": data_quality,
        "missing_fields": data_quality["blocking_missing_fields"] + data_quality["warning_missing_fields"],
        "effective_data_notes": time_context.get("time_warnings") or [],
    }
