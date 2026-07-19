"""Minimal time context builder for the first decision pipeline."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .decision_utils import get_in


DEFAULT_TIMEZONE = "Asia/Shanghai"
FALLBACK_TZ = timezone(timedelta(hours=8), name=DEFAULT_TIMEZONE)
MAX_MARKET_DATA_AGE_DAYS = 4


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


def _parse_market_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    try:
        parsed = date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
    return parsed if parsed.weekday() < 5 else None


def _latest_effective_market_date(raw_data: dict[str, Any]) -> str | None:
    technical = raw_data.get("technical") or {}
    candidates = [technical.get("last_date")]
    recent_rows = technical.get("recent_10d") or []
    if recent_rows:
        candidates.append((recent_rows[-1] or {}).get("date"))

    valid = [parsed for value in candidates if (parsed := _parse_market_date(value))]
    if valid:
        return max(valid).isoformat()

    # Legacy and replay payloads may only carry the quote date.
    quote_date = _parse_market_date(get_in(raw_data, "quote.trade_date"))
    return quote_date.isoformat() if quote_date else None


def build_time_context(
    raw_data: dict[str, Any],
    task_type: str,
    now: datetime | None = None,
    trade_date_override: str | None = None,
) -> dict[str, Any]:
    tz = _tzinfo()
    decision_time = now.astimezone(tz) if now else datetime.now(tz)
    source_quote_trade_date = get_in(raw_data, "quote.trade_date")
    effective_market_date = _latest_effective_market_date(raw_data)
    exchange = get_in(raw_data, "quote.market")
    requested_trade_date = trade_date_override or effective_market_date
    trade_date = effective_market_date
    session_name, is_trading_day = _session_name(decision_time, trade_date)

    warnings: list[str] = []
    blocked_reasons: list[str] = []
    market_data_age_days: int | None = None
    if not effective_market_date:
        blocked_reasons.append("no valid quote or daily K-line trade date is available")
    else:
        effective_date = date.fromisoformat(effective_market_date)
        market_data_age_days = (decision_time.date() - effective_date).days
        if market_data_age_days < 0:
            blocked_reasons.append("latest effective market date is in the future")
        elif market_data_age_days > MAX_MARKET_DATA_AGE_DAYS:
            blocked_reasons.append(
                f"latest effective market date is stale by {market_data_age_days} calendar days"
            )
    if source_quote_trade_date and not _parse_market_date(source_quote_trade_date):
        blocked_reasons.append("quote.trade_date is not a valid A-share trading date")
    if effective_market_date and source_quote_trade_date and effective_market_date != str(source_quote_trade_date)[:10]:
        blocked_reasons.append("quote.trade_date conflicts with the latest effective daily K-line date")
    if requested_trade_date and effective_market_date and str(requested_trade_date)[:10] != effective_market_date:
        blocked_reasons.append("requested trade_date conflicts with the latest effective market date")
    if session_name == "NON_TRADING":
        warnings.append(
            "decision time is outside the effective trading date; "
            "research may use the latest completed market data but trading is forbidden"
        )

    warnings.extend(blocked_reasons)

    return {
        "timezone": DEFAULT_TIMEZONE,
        "calendar_date": decision_time.date().isoformat(),
        "generated_at": decision_time.isoformat(),
        "decision_time": decision_time.isoformat(),
        "task_type": task_type,
        "exchange": exchange,
        "trade_date": trade_date,
        "source_quote_trade_date": source_quote_trade_date,
        "market_data_age_days": market_data_age_days,
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
        "data_status": "BLOCKED" if blocked_reasons else "GOOD",
        "blocked_data_reasons": blocked_reasons,
        "time_warnings": warnings,
    }
