"""Optional JSON-to-SQLite mirror used by runtime commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..file_store import append_jsonl
from ..portfolio import now_iso
from .connection import connect, resolve_db_path
from .migrations import init_database
from .writers import (
    write_account,
    write_account_snapshot,
    write_allocation_plan,
    write_cash_ledger,
    write_closed_position,
    write_decision_result,
    write_intraday_scan,
    write_order_intent,
    write_paper_order,
    write_paper_signal,
    write_paper_trade,
    write_position,
    write_position_ledger,
    write_position_lock,
    write_position_snapshot,
    write_pre_market_plan,
    write_report,
    write_risk_check,
    write_strategy_snapshot,
    write_trigger_event,
    write_trigger_price_list,
    write_workflow_run,
)


RecordWriter = Callable[..., None]

_RECORD_WRITERS: dict[str, RecordWriter] = {
    "account": write_account,
    "account_snapshot": write_account_snapshot,
    "strategy_snapshot": write_strategy_snapshot,
    "decision_result": write_decision_result,
    "position": write_position,
    "position_lock": write_position_lock,
    "cash_ledger": write_cash_ledger,
    "position_ledger": write_position_ledger,
    "position_snapshot": write_position_snapshot,
    "closed_position": write_closed_position,
    "paper_signal": write_paper_signal,
    "paper_order": write_paper_order,
    "paper_trade": write_paper_trade,
    "risk_check": write_risk_check,
    "allocation_plan": write_allocation_plan,
    "order_intent": write_order_intent,
    "workflow_run": write_workflow_run,
    "pre_market_plan": write_pre_market_plan,
    "trigger_price_list": write_trigger_price_list,
    "intraday_scan": write_intraday_scan,
    "trigger_event": write_trigger_event,
}


def mirror_record(
    output_root: Path,
    record_type: str,
    record: dict[str, Any],
    *,
    source_path: Path | None = None,
    db_path: str | None = None,
    required: bool = False,
) -> dict[str, Any]:
    writer = _RECORD_WRITERS.get(record_type)
    if writer is None:
        raise ValueError(f"unsupported record type: {record_type}")
    return _mirror(
        output_root,
        db_path,
        required,
        record_type,
        lambda conn: writer(conn, output_root, record, source_path=source_path),
    )


def mirror_records(
    output_root: Path,
    record_type: str,
    records: list[dict[str, Any]],
    *,
    source_path: Path | None = None,
    db_path: str | None = None,
    required: bool = False,
) -> dict[str, Any]:
    writer = _RECORD_WRITERS.get(record_type)
    if writer is None:
        raise ValueError(f"unsupported record type: {record_type}")
    return _mirror(
        output_root,
        db_path,
        required,
        record_type,
        lambda conn: [writer(conn, output_root, record, source_path=source_path) for record in records],
    )


def mirror_report(
    output_root: Path,
    path: Path,
    *,
    db_path: str | None = None,
    required: bool = False,
    report_type: str | None = None,
    title: str | None = None,
    account_id: str | None = None,
    symbol: str | None = None,
    trade_date: str | None = None,
    strategy_version: str | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _mirror(
        output_root,
        db_path,
        required,
        "report",
        lambda conn: write_report(
            conn,
            output_root,
            path,
            report_type=report_type,
            title=title,
            account_id=account_id,
            symbol=symbol,
            trade_date=trade_date,
            strategy_version=strategy_version,
            source_type=source_type,
            source_id=source_id,
            metadata=metadata,
        ),
    )


def _mirror(
    output_root: Path,
    db_path: str | None,
    required: bool,
    sync_type: str,
    callback: Callable[[Any], None],
) -> dict[str, Any]:
    resolved = resolve_db_path(output_root, db_path)
    if not resolved.exists() and not required:
        return {"status": "SKIPPED", "reason": "DB_MISSING", "db_path": str(resolved)}

    try:
        init_database(output_root, db_path)
        with connect(resolved) as conn:
            callback(conn)
            conn.commit()
    except Exception as exc:
        _log_sync_error(output_root, sync_type, resolved, exc)
        if required:
            raise
        return {
            "status": "ERROR",
            "reason": exc.__class__.__name__,
            "message": str(exc),
            "db_path": str(resolved),
        }
    return {"status": "SUCCESS", "db_path": str(resolved)}


def _log_sync_error(output_root: Path, sync_type: str, db_path: Path, exc: Exception) -> None:
    append_jsonl(
        output_root / "logs" / "db_sync_errors.jsonl",
        {
            "created_at": now_iso(),
            "sync_type": sync_type,
            "db_path": str(db_path),
            "error_type": exc.__class__.__name__,
            "error_message": str(exc),
        },
    )
