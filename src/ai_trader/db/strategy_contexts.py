"""Current and revisioned user inputs consumed by decision strategies."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from typing import Any, Mapping

from ..portfolio import now_iso


SYMBOL_PATTERN = re.compile(r"^\d{6}$")
TEXT_FIELDS = (
    "core_logic",
    "catalyst",
    "medium_term_improvement",
    "tracking_metric",
    "fundamental_invalidation",
    "technical_invalidation",
    "event_invalidation",
)
BOOLEAN_FIELDS = (
    "high_risk",
    "business_model_stable",
    "profit_quality_5y",
    "cashflow_reliable",
    "competition_not_worse",
    "logic_still_valid",
    "thesis_fully_realized",
    "would_rebuy_now",
)
CONTEXT_FIELDS = (
    "holding_period",
    "stock_type",
    "future_eps",
    *BOOLEAN_FIELDS,
    *TEXT_FIELDS,
)


def normalize_strategy_context(symbol: str, values: Mapping[str, Any]) -> dict[str, Any]:
    normalized_symbol = str(symbol or "").strip()
    if not SYMBOL_PATTERN.fullmatch(normalized_symbol):
        raise ValueError("股票代码必须是 6 位数字")

    holding_period = _choice(values.get("holding_period"), {"short", "middle", "long"})
    stock_type = _choice(values.get("stock_type"), {"STABLE", "GROWTH", "CYCLICAL", "TURNAROUND"})
    future_eps = _positive_number(values.get("future_eps"), "未来第 3 年 EPS")
    result: dict[str, Any] = {
        "symbol": normalized_symbol,
        "holding_period": holding_period,
        "stock_type": stock_type,
        "future_eps": future_eps,
    }
    for field in BOOLEAN_FIELDS:
        result[field] = _optional_bool(values.get(field), field)
    for field in TEXT_FIELDS:
        result[field] = str(values.get(field) or "").strip() or None
    return result


def get_strategy_context(conn: sqlite3.Connection, symbol: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM strategy_context_profiles WHERE symbol = ?",
        (str(symbol).strip(),),
    ).fetchone()
    if row is None:
        return None
    item = dict(row)
    payload = json.loads(item.get("payload_json") or "{}")
    return {**item, **{field: payload.get(field, item.get(field)) for field in CONTEXT_FIELDS}}


def save_strategy_context(
    conn: sqlite3.Connection,
    symbol: str,
    values: Mapping[str, Any],
    *,
    source: str = "WEB",
) -> dict[str, Any]:
    context = normalize_strategy_context(symbol, values)
    profile_id = f"strategy_context_{context['symbol']}"
    timestamp = now_iso()
    payload_json = json.dumps(context, ensure_ascii=False, sort_keys=True)
    columns = ", ".join(CONTEXT_FIELDS)
    placeholders = ", ".join("?" for _ in CONTEXT_FIELDS)
    updates = ", ".join(f"{field}=excluded.{field}" for field in CONTEXT_FIELDS)
    conn.execute(
        f"""
        INSERT INTO strategy_context_profiles (
            profile_id, symbol, {columns}, source, payload_json, created_at, updated_at
        ) VALUES (?, ?, {placeholders}, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            {updates}, source=excluded.source, payload_json=excluded.payload_json,
            updated_at=excluded.updated_at
        """,
        (
            profile_id,
            context["symbol"],
            *(context[field] for field in CONTEXT_FIELDS),
            source,
            payload_json,
            timestamp,
            timestamp,
        ),
    )
    conn.execute(
        """
        INSERT INTO strategy_context_revisions (
            revision_id, profile_id, symbol, source, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (f"scr_{uuid.uuid4().hex}", profile_id, context["symbol"], source, payload_json, timestamp),
    )
    return {**context, "profile_id": profile_id, "source": source, "updated_at": timestamp}


def list_strategy_context_revisions(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT revision_id, profile_id, symbol, source, payload_json, created_at
        FROM strategy_context_revisions
        WHERE symbol = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (str(symbol).strip(), int(limit)),
    ).fetchall()
    return [
        {**dict(row), "payload": json.loads(row["payload_json"] or "{}")}
        for row in rows
    ]


def _choice(value: Any, allowed: set[str]) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text not in allowed:
        raise ValueError(f"不支持的选项：{text}")
    return text


def _positive_number(value: Any, label: str) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    number = float(text)
    if number <= 0:
        raise ValueError(f"{label}必须大于 0")
    return number


def _optional_bool(value: Any, field: str) -> bool | None:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "是"}:
        return True
    if text in {"0", "false", "no", "否"}:
        return False
    raise ValueError(f"{field} 必须是是、否或未知")
