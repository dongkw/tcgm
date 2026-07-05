"""Historical replay clock helpers."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

from .timekeeper import _tzinfo, build_time_context


def replay_now(trade_date: str, session_name: str = "PRE_MARKET") -> datetime:
    clock_time = {
        "PRE_MARKET": time(8, 45),
        "POST_MARKET": time(15, 30),
    }.get(session_name, time(8, 45))
    return datetime.combine(datetime.fromisoformat(trade_date).date(), clock_time, tzinfo=_tzinfo())


def replay_time_context(
    *,
    target_trade_date: str,
    source_quote_trade_date: str | None,
    task_type: str,
    session_name: str = "PRE_MARKET",
    exchange: str | None = "A_STOCK",
) -> dict[str, Any]:
    raw_data = {
        "quote": {
            "trade_date": source_quote_trade_date,
            "market": exchange,
        }
    }
    return build_time_context(
        raw_data,
        task_type,
        now=replay_now(target_trade_date, session_name),
        trade_date_override=target_trade_date,
    )
