"""Minimal time context builder for the first decision pipeline."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .decision_utils import get_in


DEFAULT_TIMEZONE = "Asia/Shanghai"
FALLBACK_TZ = timezone(timedelta(hours=8), name=DEFAULT_TIMEZONE)


def _tzinfo():
    try:
        return ZoneInfo(DEFAULT_TIMEZONE)
    except ZoneInfoNotFoundError:
        return FALLBACK_TZ


def _session_name(now: datetime, trade_date: str | None) -> tuple[str, bool]:
    current_date = now.date().isoformat()
    weekday_trading_candidate = now.weekday() < 5
    is_same_trade_date = bool(trade_date and trade_date == current_date)

    if not weekday_trading_candidate or not is_same_trade_date:
        return "NON_TRADING", False

    t = now.time()
    if time(8, 30) <= t < time(9, 15):
        return "PRE_MARKET", True
    if time(9, 15) <= t < time(9, 25):
        return "CALL_AUCTION", True
    if time(9, 30) <= t <= time(11, 30):
        return "MORNING_TRADE", True
    if time(11, 30) < t < time(13, 0):
        return "LUNCH_BREAK", True
    if time(13, 0) <= t <= time(15, 0):
        return "AFTERNOON_TRADE", True
    if time(15, 0) < t <= time(18, 0):
        return "POST_MARKET", True
    return "AFTER_HOURS", True


def build_time_context(
    raw_data: dict[str, Any],
    task_type: str,
    now: datetime | None = None,
    trade_date_override: str | None = None,
) -> dict[str, Any]:
    tz = _tzinfo()
    decision_time = now.astimezone(tz) if now else datetime.now(tz)
    source_quote_trade_date = get_in(raw_data, "quote.trade_date")
    trade_date = trade_date_override or source_quote_trade_date
    exchange = get_in(raw_data, "quote.market")
    session_name, is_trading_day = _session_name(decision_time, trade_date)

    warnings: list[str] = []
    if not source_quote_trade_date:
        warnings.append("quote.trade_date is missing")
    if not trade_date:
        warnings.append("target trade_date is missing")
    if trade_date and source_quote_trade_date and trade_date != source_quote_trade_date:
        warnings.append("quote.trade_date differs from target trade_date; source data is treated as prior visible data")
    if session_name == "NON_TRADING":
        warnings.append("current time is not the target trade date; analysis is allowed, trading action needs review")

    return {
        "timezone": DEFAULT_TIMEZONE,
        "calendar_date": decision_time.date().isoformat(),
        "decision_time": decision_time.isoformat(),
        "task_type": task_type,
        "exchange": exchange,
        "trade_date": trade_date,
        "source_quote_trade_date": source_quote_trade_date,
        "is_trading_day": is_trading_day,
        "session_name": session_name,
        "previous_trade_date": source_quote_trade_date,
        "next_trade_date": None,
        "effective_data_cutoff": decision_time.isoformat(),
        "allowed_data_types": [
            "DAILY_CLOSE",
            "FINANCIAL_REPORT",
            "ANNOUNCEMENT",
            "POSITION_SNAPSHOT",
        ],
        "blocked_data_reasons": [],
        "time_warnings": warnings,
    }
