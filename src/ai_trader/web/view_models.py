"""Small formatting helpers for dashboard templates."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def money(value: Any) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def pct(value: Any) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return str(value)


def dash(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return str(value)


def datetime_text(value: Any) -> str:
    if value is None or value == "":
        return "-"
    text = str(value)
    if "T" not in text:
        return text
    try:
        return datetime.fromisoformat(text).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text


def status_class(value: Any) -> str:
    text = str(value or "").upper()
    if text in {
        "ERROR",
        "FAILED",
        "REJECTED",
        "BLOCKED",
        "DATA_BLOCKED",
        "RISK",
        "IGNORE",
        "HIGH",
        "SELL_RISK",
        "UNFAVORABLE",
        "OPPOSE",
        "STRONG_OPPOSE",
        "EXIT_SUPPORT",
    }:
        return "status-danger"
    if text in {"WARNING", "WARN", "STALE", "MISSING", "NO_SELL_T_PLUS", "DATA_GAP", "DEFER", "MEDIUM", "POSITION_RISK", "TRADE_LIMIT", "NEED_DECISION", "PARTIAL", "MIXED", "REDUCE_SUPPORT"}:
        return "status-warning"
    if text in {
        "OK",
        "SUCCESS",
        "READY",
        "READY_FOR_CONFIRM",
        "FILLED",
        "ACTIVE",
        "CANDIDATE",
        "BUY_CANDIDATE",
        "GENERATE_DECISION",
        "DECISION_CREATED",
        "LOW",
        "OK",
        "TAKE_PROFIT",
        "FAVORABLE",
        "SUPPORT",
        "STRONG_SUPPORT",
        "HOLD_SUPPORT",
    }:
        return "status-ok"
    if text in {
        "RECORDED",
        "WAIT",
        "HOLD",
        "WATCH_SMALL",
        "WATCH",
        "WATCH_TOMORROW",
        "WATCH_INTRADAY",
        "NEUTRAL",
        "WATCH_TODAY",
        "UNREVIEWED",
        "VIEWED",
        "DONE",
        "RUNNING",
        "PENDING",
    }:
        return "status-info"
    return "status-muted"


def short_text(value: Any, limit: int = 80) -> str:
    text = dash(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."
