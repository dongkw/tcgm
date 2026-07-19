"""Database-first historical market data helpers."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from ..portfolio import now_iso
from .watchlists import normalize_symbol


def payload_hash(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def create_market_data_batch(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    batch_type: str,
    session_type: str,
    scope_type: str,
    scope: dict[str, Any],
    total_count: int,
    source: str,
    params: dict[str, Any] | None = None,
    started_at: str | None = None,
) -> None:
    now = now_iso()
    conn.execute(
        """
        INSERT INTO market_data_batches (
            batch_id, batch_type, session_type, scope_type, scope_json,
            started_at, status, total_count, source, params_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 'RUNNING', ?, ?, ?, ?)
        """,
        (
            batch_id,
            batch_type,
            session_type,
            scope_type,
            json.dumps(scope, ensure_ascii=False),
            started_at or now,
            total_count,
            source,
            json.dumps(params or {}, ensure_ascii=False),
            now,
        ),
    )


def finish_market_data_batch(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    trade_date: str | None,
    status: str,
    success_count: int,
    failed_count: int,
    error_message: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        UPDATE market_data_batches
        SET trade_date=?, finished_at=?, status=?, success_count=?,
            failed_count=?, error_message=?, payload_json=?
        WHERE batch_id=?
        """,
        (
            trade_date,
            now_iso(),
            status,
            success_count,
            failed_count,
            error_message,
            json.dumps(payload or {}, ensure_ascii=False),
            batch_id,
        ),
    )


def fail_market_data_batch(conn: sqlite3.Connection, *, batch_id: str, error_message: str) -> None:
    conn.execute(
        """
        UPDATE market_data_batches
        SET finished_at=?, status='FAILED', error_message=?
        WHERE batch_id=?
        """,
        (now_iso(), error_message, batch_id),
    )


def upsert_daily_market_snapshot(
    conn: sqlite3.Connection,
    symbol: str,
    payload: dict[str, Any],
    *,
    batch_id: str | None,
    data_origin: str = "LIVE_CAPTURE",
    source_version: str | None = None,
) -> bool:
    normalized = normalize_symbol(symbol or (payload.get("quote") or {}).get("code") or "")
    if not normalized:
        return False
    quote = payload.get("quote") or {}
    trade_date = quote.get("trade_date")
    if not trade_date:
        return False
    technical = payload.get("technical") or {}
    valuation = payload.get("valuation") or {}
    meta = payload.get("meta") or {}
    now = now_iso()
    origin = data_origin.strip().upper() or "LIVE_CAPTURE"
    is_backfilled = 1 if origin == "BACKFILL" else 0
    snapshot_id = f"dms_{normalized}_{trade_date}"
    quality_flags = _quality_flags(quote, technical)
    quality_status = "OK" if not quality_flags else "PARTIAL"
    payload_text = json.dumps(payload, ensure_ascii=False)
    conn.execute(
        """
        INSERT INTO daily_market_snapshots (
            snapshot_id, batch_id, symbol, name, exchange, asset_type, trade_date,
            open, high, low, close, pre_close, pct_change, volume, amount,
            turnover_pct, pe_ttm, pb, market_cap_yuan, ma20, ma60,
            change_20d_pct, atr14_pct, source, observed_at, effective_from,
            quality_status, quality_flags_json, payload_hash, payload_json,
            data_origin, is_backfilled, backfilled_at, backfill_batch_id,
            source_version, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, 'stock', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, trade_date) DO UPDATE SET
            batch_id=excluded.batch_id,
            name=COALESCE(excluded.name, daily_market_snapshots.name),
            exchange=COALESCE(excluded.exchange, daily_market_snapshots.exchange),
            open=excluded.open,
            high=excluded.high,
            low=excluded.low,
            close=excluded.close,
            pre_close=excluded.pre_close,
            pct_change=excluded.pct_change,
            volume=excluded.volume,
            amount=excluded.amount,
            turnover_pct=excluded.turnover_pct,
            pe_ttm=excluded.pe_ttm,
            pb=excluded.pb,
            market_cap_yuan=excluded.market_cap_yuan,
            ma20=excluded.ma20,
            ma60=excluded.ma60,
            change_20d_pct=excluded.change_20d_pct,
            atr14_pct=excluded.atr14_pct,
            source=excluded.source,
            observed_at=excluded.observed_at,
            effective_from=excluded.effective_from,
            quality_status=excluded.quality_status,
            quality_flags_json=excluded.quality_flags_json,
            payload_hash=excluded.payload_hash,
            payload_json=excluded.payload_json,
            data_origin=excluded.data_origin,
            is_backfilled=excluded.is_backfilled,
            backfilled_at=excluded.backfilled_at,
            backfill_batch_id=excluded.backfill_batch_id,
            source_version=excluded.source_version,
            updated_at=excluded.updated_at
        """,
        (
            snapshot_id,
            batch_id,
            normalized,
            quote.get("name"),
            quote.get("market"),
            trade_date,
            quote.get("open"),
            quote.get("high"),
            quote.get("low"),
            quote.get("price"),
            quote.get("pre_close"),
            quote.get("pct_change"),
            quote.get("volume"),
            quote.get("amount"),
            quote.get("turnover_pct"),
            _first_present(valuation.get("pe_ttm"), quote.get("pe_ttm")),
            _first_present(valuation.get("pb"), quote.get("pb")),
            quote.get("market_cap_yuan"),
            technical.get("ma20"),
            technical.get("ma60"),
            technical.get("change_20d_pct"),
            technical.get("atr14_pct"),
            meta.get("source"),
            now,
            quote.get("quote_time") or meta.get("generated_at") or now,
            quality_status,
            json.dumps(quality_flags, ensure_ascii=False),
            payload_hash(payload),
            payload_text,
            origin,
            is_backfilled,
            now if is_backfilled else None,
            batch_id if is_backfilled else None,
            source_version,
            now,
            now,
        ),
    )
    return True


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _quality_flags(quote: dict[str, Any], technical: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if not quote.get("trade_date"):
        flags.append("MISSING_TRADE_DATE")
    if quote.get("price") is None:
        flags.append("MISSING_CLOSE")
    if technical.get("ma20") is None:
        flags.append("MISSING_MA20")
    if technical.get("ma60") is None:
        flags.append("MISSING_MA60")
    return flags
