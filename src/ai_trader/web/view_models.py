"""Small formatting helpers for dashboard templates."""

from __future__ import annotations

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


def status_class(value: Any) -> str:
    text = str(value or "").upper()
    if text in {"ERROR", "FAILED", "REJECTED", "BLOCKED", "DATA_BLOCKED"}:
        return "status-danger"
    if text in {"WARNING", "WARN", "STALE", "MISSING", "NO_SELL_T_PLUS"}:
        return "status-warning"
    if text in {"OK", "SUCCESS", "READY", "READY_FOR_CONFIRM", "FILLED", "ACTIVE"}:
        return "status-ok"
    if text in {"RECORDED", "WAIT", "HOLD", "WATCH_SMALL"}:
        return "status-info"
    return "status-muted"


def short_text(value: Any, limit: int = 80) -> str:
    text = dash(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."
