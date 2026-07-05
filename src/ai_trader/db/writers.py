"""Focused SQLite writers for newly generated artifacts.

These helpers mirror the importer mappings so the JSON/Markdown files remain
the source artifact while SQLite gets queryable indexes immediately.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from ..portfolio import now_iso
from .repositories import bool_int, json_text, upsert


_COLUMN_CACHE: dict[str, set[str]] = {}


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_path(output_root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(output_root.resolve()))
    except ValueError:
        return str(path)


def artifact_id(output_root: Path, path: Path) -> str:
    return "art_" + hash_text(relative_path(output_root, path))[:24]


def row_id(prefix: str, *parts: Any) -> str:
    raw = "|".join("" if part is None else str(part) for part in parts)
    return f"{prefix}_{hash_text(raw)[:24]}"


def file_created_at(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat()


def mime_type(path: Path) -> str:
    if path.suffix == ".json":
        return "application/json"
    if path.suffix == ".jsonl":
        return "application/x-jsonlines"
    if path.suffix == ".md":
        return "text/markdown"
    return "application/octet-stream"


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if table not in _COLUMN_CACHE:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        _COLUMN_CACHE[table] = {row["name"] for row in rows}
    return _COLUMN_CACHE[table]


def filtered_upsert(
    conn: sqlite3.Connection,
    table: str,
    record: dict[str, Any],
    conflict_columns: list[str],
) -> None:
    allowed = table_columns(conn, table)
    filtered = {key: value for key, value in record.items() if key in allowed}
    missing_conflict = [
        column
        for column in conflict_columns
        if column not in filtered or filtered.get(column) is None
    ]
    if missing_conflict:
        raise ValueError(f"missing conflict column(s) for {table}: {', '.join(missing_conflict)}")
    upsert(conn, table, filtered, conflict_columns)


def record_artifact(
    conn: sqlite3.Connection,
    output_root: Path,
    path: Path,
    artifact_type: str,
    source_module: str,
    *,
    record_count: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    metadata = metadata or {}
    current_artifact_id = artifact_id(output_root, path)
    filtered_upsert(
        conn,
        "file_artifacts",
        {
            "artifact_id": current_artifact_id,
            "artifact_type": artifact_type,
            "source_module": source_module,
            "relative_path": relative_path(output_root, path),
            "absolute_path": str(path.resolve()) if path.exists() else str(path),
            "content_hash": hash_file(path) if path.exists() else None,
            "mime_type": mime_type(path),
            "record_count": record_count,
            "trade_date": metadata.get("trade_date"),
            "symbol": metadata.get("symbol"),
            "account_id": metadata.get("account_id"),
            "strategy_version": metadata.get("strategy_version"),
            "created_at": file_created_at(path),
            "indexed_at": now_iso(),
            "metadata_json": json_text(metadata),
        },
        ["artifact_id"],
    )
    return current_artifact_id


def write_strategy_snapshot(
    conn: sqlite3.Connection,
    output_root: Path,
    snapshot: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> None:
    data_quality = snapshot.get("data_quality") or {}
    time_context = snapshot.get("time_context") or {}
    current_artifact_id = None
    if source_path is not None:
        current_artifact_id = record_artifact(
            conn,
            output_root,
            source_path,
            "json",
            "strategy_snapshot",
            metadata={
                "symbol": snapshot.get("symbol"),
                "trade_date": snapshot.get("trade_date") or time_context.get("trade_date"),
                "strategy_version": snapshot.get("strategy_version"),
            },
        )
    filtered_upsert(
        conn,
        "strategy_snapshots",
        {
            "snapshot_id": snapshot.get("snapshot_id"),
            "symbol": snapshot.get("symbol"),
            "name": snapshot.get("name"),
            "task_type": snapshot.get("task_type"),
            "trade_date": snapshot.get("trade_date") or time_context.get("trade_date"),
            "decision_time": snapshot.get("decision_time"),
            "strategy_version": snapshot.get("strategy_version"),
            "schema_version": snapshot.get("schema_version"),
            "data_quality_level": data_quality.get("level"),
            "data_quality_score": data_quality.get("score"),
            "payload_json": json_text(snapshot),
            "source_file": snapshot.get("source_file"),
            "artifact_id": current_artifact_id,
            "created_at": snapshot.get("decision_time"),
        },
        ["snapshot_id"],
    )


def write_decision_result(
    conn: sqlite3.Connection,
    output_root: Path,
    decision: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> None:
    time_context = decision.get("time_context") or {}
    current_artifact_id = None
    if source_path is not None:
        current_artifact_id = record_artifact(
            conn,
            output_root,
            source_path,
            "json",
            "decision_result",
            metadata={
                "symbol": decision.get("symbol"),
                "trade_date": decision.get("trade_date") or time_context.get("trade_date"),
                "strategy_version": decision.get("strategy_version"),
            },
        )
    filtered_upsert(
        conn,
        "decision_results",
        {
            "decision_id": decision.get("decision_id"),
            "snapshot_id": decision.get("snapshot_id"),
            "symbol": decision.get("symbol"),
            "name": decision.get("name"),
            "task_type": decision.get("task_type"),
            "trade_date": decision.get("trade_date") or time_context.get("trade_date"),
            "decision_time": decision.get("decision_time"),
            "strategy_version": decision.get("strategy_version"),
            "schema_version": decision.get("schema_version"),
            "final_action": decision.get("final_action"),
            "confidence": decision.get("confidence"),
            "action_reason": decision.get("action_reason"),
            "human_review_required": bool_int(decision.get("human_review_required")),
            "trigger_prices_json": json_text(decision.get("trigger_prices") or {}),
            "payload_json": json_text(decision),
            "artifact_id": current_artifact_id,
            "created_at": decision.get("decision_time"),
        },
        ["decision_id"],
    )


def write_risk_check(
    conn: sqlite3.Connection,
    output_root: Path,
    record: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> None:
    if source_path is not None:
        record_artifact(
            conn,
            output_root,
            source_path,
            "jsonl",
            "risk_checks",
            metadata={
                "account_id": record.get("account_id"),
                "symbol": record.get("symbol"),
                "trade_date": record.get("trade_date"),
            },
        )
    filtered_upsert(
        conn,
        "risk_checks",
        {
            "risk_check_id": record.get("risk_check_id") or row_id("risk", source_path, record.get("decision_id"), record.get("created_at")),
            "account_id": record.get("account_id"),
            "decision_id": record.get("decision_id"),
            "snapshot_id": record.get("snapshot_id"),
            "symbol": record.get("symbol"),
            "name": record.get("name"),
            "trade_date": record.get("trade_date"),
            "risk_status": record.get("risk_status"),
            "risk_level": record.get("risk_level"),
            "allowed_action": record.get("allowed_action"),
            "original_action": record.get("original_action"),
            "max_cash_amount": record.get("max_cash_amount"),
            "max_quantity": record.get("max_quantity"),
            "reference_price": record.get("reference_price"),
            "quote_source": record.get("quote_source"),
            "blocking_rules_json": json_text(record.get("blocking_rules") or []),
            "warning_rules_json": json_text(record.get("warning_rules") or []),
            "human_review_required": bool_int(record.get("human_review_required")),
            "execution_allowed": bool_int(record.get("execution_allowed")),
            "source_decision_path": record.get("source_decision_path"),
            "source_snapshot_path": record.get("source_snapshot_path"),
            "created_at": record.get("created_at"),
            "payload_json": json_text(record),
        },
        ["risk_check_id"],
    )


def write_allocation_plan(
    conn: sqlite3.Connection,
    output_root: Path,
    record: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> None:
    if source_path is not None:
        record_artifact(
            conn,
            output_root,
            source_path,
            "jsonl",
            "allocation_plans",
            metadata={"account_id": record.get("account_id"), "trade_date": record.get("trade_date")},
        )
    row = dict(record)
    row["payload_json"] = json_text(record)
    filtered_upsert(conn, "allocation_plans", row, ["allocation_id"])


def write_order_intent(
    conn: sqlite3.Connection,
    output_root: Path,
    record: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> None:
    if source_path is not None:
        record_artifact(
            conn,
            output_root,
            source_path,
            "jsonl",
            "order_intents",
            metadata={"account_id": record.get("account_id"), "symbol": record.get("symbol")},
        )
    row = dict(record)
    row["payload_json"] = json_text(record)
    filtered_upsert(conn, "order_intents", row, ["intent_id"])


def write_workflow_run(
    conn: sqlite3.Connection,
    output_root: Path,
    run: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> None:
    params = run.get("input_params_json") or {}
    if source_path is not None:
        record_artifact(
            conn,
            output_root,
            source_path,
            "jsonl",
            "workflow_runs",
            metadata={"account_id": params.get("account"), "trade_date": run.get("trade_date")},
        )
    filtered_upsert(
        conn,
        "workflow_runs",
        {
            "workflow_run_id": run.get("workflow_run_id"),
            "workflow_type": run.get("workflow_type"),
            "account_id": params.get("account"),
            "trade_date": run.get("trade_date"),
            "calendar_date": run.get("calendar_date"),
            "session_name": run.get("session_name"),
            "is_trading_day": bool_int(run.get("is_trading_day")),
            "effective_data_cutoff": run.get("effective_data_cutoff"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "status": run.get("status"),
            "input_params_json": json_text(params),
            "output_refs_json": json_text(run.get("output_refs_json") or {}),
            "error_code": run.get("error_code"),
            "error_message": run.get("error_message"),
            "created_at": run.get("created_at"),
            "payload_json": json_text(run),
        },
        ["workflow_run_id"],
    )


def write_pre_market_plan(
    conn: sqlite3.Connection,
    output_root: Path,
    plan: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> None:
    current_artifact_id = None
    if source_path is not None:
        current_artifact_id = record_artifact(
            conn,
            output_root,
            source_path,
            "json",
            "pre_market_plan",
            metadata={"account_id": plan.get("account_id"), "trade_date": plan.get("trade_date")},
        )
    plan_id = row_id("plan", source_path.stem if source_path is not None else plan.get("workflow_run_id"))
    filtered_upsert(
        conn,
        "pre_market_plans",
        {
            "plan_id": plan_id,
            "workflow_run_id": plan.get("workflow_run_id"),
            "account_id": plan.get("account_id"),
            "trade_date": plan.get("trade_date"),
            "session_name": plan.get("session_name"),
            "execution_allowed": bool_int(plan.get("execution_allowed")),
            "symbols_json": json_text(plan.get("symbols") or []),
            "rollover_json": json_text(plan.get("rollover") or {}),
            "allocation_id": (plan.get("allocation_plan") or {}).get("allocation_id"),
            "trigger_price_list_path": plan.get("trigger_price_list_path"),
            "payload_json": json_text(plan),
            "artifact_id": current_artifact_id,
            "created_at": plan.get("created_at"),
        },
        ["plan_id"],
    )


def write_trigger_price_list(
    conn: sqlite3.Connection,
    output_root: Path,
    data: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> None:
    items = data.get("items") or []
    if source_path is not None:
        record_artifact(
            conn,
            output_root,
            source_path,
            "json",
            "trigger_price_list",
            record_count=len(items),
            metadata={"account_id": data.get("account_id"), "trade_date": data.get("trade_date")},
        )
    for item in items:
        triggers = item.get("trigger_prices") or {}
        filtered_upsert(
            conn,
            "trigger_price_items",
            {
                "trigger_item_id": row_id(
                    "tpi",
                    data.get("account_id"),
                    data.get("trade_date"),
                    item.get("symbol"),
                    item.get("task_type"),
                    item.get("decision_id"),
                ),
                "account_id": data.get("account_id"),
                "trade_date": data.get("trade_date"),
                "symbol": item.get("symbol"),
                "task": item.get("task"),
                "task_type": item.get("task_type"),
                "decision_id": item.get("decision_id"),
                "snapshot_id": item.get("snapshot_id"),
                "final_action": item.get("final_action"),
                "confidence": item.get("confidence"),
                "reduce_trigger_price": triggers.get("reduce_trigger_price"),
                "clear_trigger_price": triggers.get("clear_trigger_price"),
                "middle_trend_price": triggers.get("middle_trend_price"),
                "resistance_price": triggers.get("resistance_price"),
                "stop_loss_price": triggers.get("stop_loss_price"),
                "source_decision_path": item.get("source_decision_path"),
                "created_at": data.get("created_at"),
                "payload_json": json_text(item),
            },
            ["trigger_item_id"],
        )


def write_intraday_scan(
    conn: sqlite3.Connection,
    output_root: Path,
    scan: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> None:
    if source_path is not None:
        record_artifact(
            conn,
            output_root,
            source_path,
            "jsonl",
            "intraday_scans",
            metadata={"account_id": scan.get("account_id"), "trade_date": scan.get("trade_date")},
        )
    row = dict(scan)
    row["allow_non_trading"] = bool_int(scan.get("allow_non_trading"))
    row["is_trading_day"] = bool_int(scan.get("is_trading_day"))
    row["input_refs_json"] = json_text(scan.get("input_refs") or scan.get("input_refs_json") or {})
    row["created_at"] = scan.get("created_at") or scan.get("started_at")
    row["payload_json"] = json_text(scan)
    row.pop("input_refs", None)
    row.pop("time_warnings", None)
    filtered_upsert(conn, "intraday_scans", row, ["scan_id"])


def write_trigger_event(
    conn: sqlite3.Connection,
    output_root: Path,
    event: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> None:
    if source_path is not None:
        record_artifact(
            conn,
            output_root,
            source_path,
            "jsonl",
            "trigger_events",
            metadata={
                "account_id": event.get("account_id"),
                "symbol": event.get("symbol"),
                "trade_date": event.get("trade_date"),
            },
        )
    row = dict(event)
    row["execution_allowed"] = bool_int(event.get("execution_allowed"))
    row["requires_human_confirm"] = bool_int(event.get("requires_human_confirm"))
    row["payload_json"] = json_text(event)
    filtered_upsert(conn, "trigger_events", row, ["trigger_event_id"])


def write_account(
    conn: sqlite3.Connection,
    output_root: Path,
    account: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> None:
    if source_path is not None:
        record_artifact(
            conn,
            output_root,
            source_path,
            "json",
            "portfolio",
            metadata={"account_id": account.get("account_id")},
        )
    account_id = account.get("account_id")
    filtered_upsert(
        conn,
        "accounts",
        {
            "account_id": account_id,
            "account_name": account.get("account_name"),
            "account_type": account.get("account_type"),
            "base_currency": account.get("base_currency"),
            "initial_cash": account.get("initial_cash"),
            "cash_reserve_pct": account.get("cash_reserve_pct"),
            "max_single_position_pct": account.get("max_single_position_pct"),
            "max_daily_buy_amount": account.get("max_daily_buy_amount"),
            "is_active": 1,
            "created_at": account.get("created_at"),
            "updated_at": account.get("updated_at"),
            "payload_json": json_text(account),
        },
        ["account_id"],
    )
    filtered_upsert(
        conn,
        "account_states",
        {
            "account_state_id": f"state_{account_id}_current",
            "account_id": account_id,
            "as_of_time": account.get("updated_at"),
            "trade_date": account.get("last_rollover_trade_date"),
            "available_cash": account.get("available_cash"),
            "frozen_cash": account.get("frozen_cash"),
            "market_value": account.get("market_value"),
            "total_assets": account.get("total_assets"),
            "equity_position_pct": account.get("equity_position_pct"),
            "cash_pct": account.get("cash_pct"),
            "today_buy_used": account.get("today_buy_used"),
            "today_sell_amount": account.get("today_sell_amount"),
            "last_rollover_trade_date": account.get("last_rollover_trade_date"),
            "updated_at": account.get("updated_at"),
            "payload_json": json_text(account),
        },
        ["account_state_id"],
    )


def write_position(
    conn: sqlite3.Connection,
    output_root: Path,
    position: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> None:
    if source_path is not None:
        record_artifact(
            conn,
            output_root,
            source_path,
            "json",
            "portfolio",
            metadata={"account_id": position.get("account_id"), "symbol": position.get("symbol")},
        )
    filtered_upsert(
        conn,
        "positions",
        _position_row(position.get("account_id"), position.get("symbol"), position),
        ["account_id", "symbol"],
    )


def write_position_lock(
    conn: sqlite3.Connection,
    output_root: Path,
    record: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> None:
    _write_jsonl_payload_record(conn, output_root, "position_locks", "lock_id", record, source_path)


def write_cash_ledger(
    conn: sqlite3.Connection,
    output_root: Path,
    record: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> None:
    _write_jsonl_payload_record(conn, output_root, "cash_ledger", "cash_ledger_id", record, source_path)


def write_position_ledger(
    conn: sqlite3.Connection,
    output_root: Path,
    record: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> None:
    _write_jsonl_payload_record(conn, output_root, "position_ledger", "position_ledger_id", record, source_path)


def write_account_snapshot(
    conn: sqlite3.Connection,
    output_root: Path,
    record: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> None:
    _write_jsonl_payload_record(conn, output_root, "account_snapshots", "snapshot_id", record, source_path)


def write_position_snapshot(
    conn: sqlite3.Connection,
    output_root: Path,
    record: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> None:
    _write_jsonl_payload_record(conn, output_root, "position_snapshots", "snapshot_id", record, source_path)


def write_paper_signal(
    conn: sqlite3.Connection,
    output_root: Path,
    record: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> None:
    _write_jsonl_payload_record(conn, output_root, "paper_signals", "signal_id", record, source_path)


def write_paper_order(
    conn: sqlite3.Connection,
    output_root: Path,
    record: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> None:
    _write_jsonl_payload_record(conn, output_root, "paper_orders", "order_id", record, source_path)


def write_paper_trade(
    conn: sqlite3.Connection,
    output_root: Path,
    record: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> None:
    _write_jsonl_payload_record(conn, output_root, "paper_trades", "trade_id", record, source_path)


def write_closed_position(
    conn: sqlite3.Connection,
    output_root: Path,
    record: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> None:
    if source_path is not None:
        record_artifact(
            conn,
            output_root,
            source_path,
            "jsonl",
            "closed_positions",
            metadata={"account_id": record.get("account_id"), "symbol": record.get("symbol")},
        )
    filtered_upsert(
        conn,
        "closed_positions",
        {
            "closed_position_id": record.get("closed_position_id")
            or row_id("closed", record.get("account_id"), record.get("symbol"), record.get("close_date"), record.get("created_at")),
            "account_id": record.get("account_id"),
            "symbol": record.get("symbol"),
            "open_date": record.get("open_date"),
            "close_date": record.get("close_date"),
            "holding_days": record.get("holding_days"),
            "avg_buy_cost": record.get("avg_buy_cost"),
            "avg_sell_price": record.get("avg_sell_price"),
            "total_sell_amount": record.get("total_sell_amount"),
            "realized_pnl": record.get("realized_pnl"),
            "buy_logic": record.get("buy_logic"),
            "invalidation_point": record.get("invalidation_point"),
            "close_reason": record.get("close_reason"),
            "related_decision_ids_json": json_text(record.get("related_decision_ids") or []),
            "created_at": record.get("created_at"),
            "payload_json": json_text(record),
        },
        ["closed_position_id"],
    )


def _position_row(account_id: str | None, symbol: str | None, position: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_id": account_id,
        "symbol": position.get("symbol") or symbol,
        "name": position.get("name"),
        "asset_type": position.get("asset_type"),
        "total_quantity": position.get("total_quantity"),
        "available_quantity": position.get("available_quantity"),
        "locked_quantity": position.get("locked_quantity"),
        "avg_cost": position.get("avg_cost"),
        "market_price": position.get("market_price"),
        "market_value": position.get("market_value"),
        "unrealized_pnl": position.get("unrealized_pnl"),
        "unrealized_pnl_pct": position.get("unrealized_pnl_pct"),
        "position_pct": position.get("position_pct"),
        "first_buy_date": position.get("first_buy_date"),
        "last_trade_date": position.get("last_trade_date"),
        "buy_logic": position.get("buy_logic"),
        "invalidation_point": position.get("invalidation_point"),
        "stop_loss_price": position.get("stop_loss_price"),
        "planned_position_pct": position.get("planned_position_pct"),
        "position_status": position.get("position_status"),
        "updated_at": position.get("updated_at"),
        "payload_json": json_text(position),
    }


def _write_jsonl_payload_record(
    conn: sqlite3.Connection,
    output_root: Path,
    table: str,
    id_field: str,
    record: dict[str, Any],
    source_path: Path | None,
    conflict_columns: list[str] | None = None,
) -> None:
    if source_path is not None:
        record_artifact(
            conn,
            output_root,
            source_path,
            "jsonl",
            table,
            metadata={
                "account_id": record.get("account_id"),
                "symbol": record.get("symbol"),
                "trade_date": record.get("trade_date"),
            },
        )
    row = dict(record)
    if "payload_json" not in row:
        row["payload_json"] = json_text(record)
    if id_field not in row or not row.get(id_field):
        row[id_field] = row_id(table, source_path, record)
    filtered_upsert(conn, table, row, conflict_columns or [id_field])


def write_report(
    conn: sqlite3.Connection,
    output_root: Path,
    path: Path,
    *,
    report_type: str | None = None,
    title: str | None = None,
    account_id: str | None = None,
    symbol: str | None = None,
    trade_date: str | None = None,
    strategy_version: str | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    metadata = metadata or {}
    current_artifact_id = record_artifact(
        conn,
        output_root,
        path,
        "markdown",
        "reports",
        metadata={
            "account_id": account_id,
            "symbol": symbol,
            "trade_date": trade_date,
            "strategy_version": strategy_version,
            **metadata,
        },
    )
    filtered_upsert(
        conn,
        "reports",
        {
            "report_id": row_id("rpt", relative_path(output_root, path)),
            "report_type": report_type or report_type_from_name(path.name),
            "title": title or report_title(path),
            "account_id": account_id,
            "symbol": symbol or symbol_from_report_name(path.name),
            "trade_date": trade_date or date_from_name(path.name),
            "strategy_version": strategy_version,
            "source_type": source_type,
            "source_id": source_id,
            "artifact_id": current_artifact_id,
            "relative_path": relative_path(output_root, path),
            "created_at": file_created_at(path),
            "metadata_json": json_text(metadata),
        },
        ["report_id"],
    )


def report_title(path: Path) -> str | None:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                return line.strip().lstrip("#").strip()
    except UnicodeDecodeError:
        return None
    return None


def report_type_from_name(name: str) -> str:
    for suffix in ["_report", "report"]:
        if suffix in name:
            return name.split(suffix, 1)[0].strip("_") or "report"
    return Path(name).stem


def symbol_from_report_name(name: str) -> str | None:
    for part in name.split("_"):
        if part.isdigit() and len(part) == 6:
            return part
    return None


def date_from_name(name: str) -> str | None:
    for part in name.replace(".md", "").split("_"):
        if len(part) == 10 and part[4] == "-" and part[7] == "-":
            return part
    return None
