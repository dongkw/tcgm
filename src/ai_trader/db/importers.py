"""Import existing JSON/JSONL ledgers into SQLite."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from ..file_store import load_json, read_jsonl
from ..portfolio import compact_now, now_iso
from .connection import connect, resolve_db_path
from .migrations import init_database
from .repositories import bool_int, json_text, upsert


_COLUMN_CACHE: dict[str, set[str]] = {}


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(output_root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(output_root.resolve()))
    except ValueError:
        return str(path)


def _artifact_id(output_root: Path, path: Path) -> str:
    return "art_" + _hash_text(_relative(output_root, path))[:24]


def _row_id(prefix: str, *parts: Any) -> str:
    raw = "|".join("" if part is None else str(part) for part in parts)
    return f"{prefix}_{_hash_text(raw)[:24]}"


def _file_created_at(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat()


def _record_artifact(
    conn: sqlite3.Connection,
    output_root: Path,
    path: Path,
    artifact_type: str,
    source_module: str,
    *,
    record_count: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    artifact_id = _artifact_id(output_root, path)
    if path.exists():
        content_hash = _hash_file(path)
        absolute_path = str(path.resolve())
    else:
        content_hash = None
        absolute_path = str(path)
    upsert(
        conn,
        "file_artifacts",
        {
            "artifact_id": artifact_id,
            "artifact_type": artifact_type,
            "source_module": source_module,
            "relative_path": _relative(output_root, path),
            "absolute_path": absolute_path,
            "content_hash": content_hash,
            "mime_type": _mime_type(path),
            "record_count": record_count,
            "trade_date": (metadata or {}).get("trade_date"),
            "symbol": (metadata or {}).get("symbol"),
            "account_id": (metadata or {}).get("account_id"),
            "strategy_version": (metadata or {}).get("strategy_version"),
            "created_at": _file_created_at(path),
            "indexed_at": now_iso(),
            "metadata_json": json_text(metadata or {}),
        },
        ["artifact_id"],
    )
    return artifact_id


def _mime_type(path: Path) -> str:
    if path.suffix == ".json":
        return "application/json"
    if path.suffix == ".jsonl":
        return "application/x-jsonlines"
    if path.suffix == ".md":
        return "text/markdown"
    return "application/octet-stream"


def _safe_json(path: Path) -> dict[str, Any]:
    return load_json(path, {}) if path.exists() else {}


def _safe_jsonl(path: Path) -> list[dict[str, Any]]:
    return read_jsonl(path) if path.exists() else []


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if table not in _COLUMN_CACHE:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        _COLUMN_CACHE[table] = {row["name"] for row in rows}
    return _COLUMN_CACHE[table]


def _filter_columns(conn: sqlite3.Connection, table: str, record: dict[str, Any]) -> dict[str, Any]:
    allowed = _table_columns(conn, table)
    return {key: value for key, value in record.items() if key in allowed}


def _insert(conn: sqlite3.Connection, table: str, record: dict[str, Any], conflict: list[str], stats: Counter[str]) -> None:
    filtered = _filter_columns(conn, table, record)
    missing_conflict = [column for column in conflict if column not in filtered or filtered.get(column) is None]
    if missing_conflict:
        raise ValueError(f"missing conflict column(s) for {table}: {', '.join(missing_conflict)}")
    upsert(conn, table, filtered, conflict)
    stats[table] += 1


def _batch_start(conn: sqlite3.Connection, output_root: Path) -> str:
    batch_id = f"imp_{compact_now()}"
    upsert(
        conn,
        "import_batches",
        {
            "batch_id": batch_id,
            "source_type": "json_ledger",
            "source_path": str(output_root),
            "started_at": now_iso(),
            "finished_at": None,
            "status": "RUNNING",
            "record_count": 0,
            "success_count": 0,
            "failed_count": 0,
            "error_message": None,
            "metadata_json": None,
            "created_at": now_iso(),
        },
        ["batch_id"],
    )
    return batch_id


def _batch_finish(
    conn: sqlite3.Connection,
    batch_id: str,
    status: str,
    stats: Counter[str],
    error_message: str | None = None,
) -> None:
    total = sum(stats.values())
    conn.execute(
        """
        UPDATE import_batches
        SET finished_at=?, status=?, record_count=?, success_count=?, failed_count=?, error_message=?, metadata_json=?
        WHERE batch_id=?
        """,
        (
            now_iso(),
            status,
            total,
            total if status == "SUCCESS" else 0,
            0 if status == "SUCCESS" else total,
            error_message,
            json_text(dict(stats)),
            batch_id,
        ),
    )


def import_json_ledgers(output_root: Path, db_path: str | None = None) -> dict[str, Any]:
    init_database(output_root, db_path)
    resolved = resolve_db_path(output_root, db_path)
    stats: Counter[str] = Counter()
    with connect(resolved) as conn:
        batch_id = _batch_start(conn, output_root)
        try:
            _import_portfolio(conn, output_root, stats)
            _import_paper_trading(conn, output_root, stats)
            _import_decisions(conn, output_root, stats)
            _import_risk_and_allocation(conn, output_root, stats)
            _import_workflows(conn, output_root, stats)
            _import_intraday(conn, output_root, stats)
            _import_replays(conn, output_root, stats)
            _import_strategy_iterations(conn, output_root, stats)
            _import_reports(conn, output_root, stats)
            _batch_finish(conn, batch_id, "SUCCESS", stats)
            conn.commit()
        except Exception as exc:
            _batch_finish(conn, batch_id, "FAILED", stats, str(exc))
            conn.commit()
            raise
    return {"db_path": str(resolved), "batch_id": batch_id, "stats": dict(stats)}


def _import_portfolio(conn: sqlite3.Connection, root: Path, stats: Counter[str]) -> None:
    portfolio = root / "portfolio"
    accounts_path = portfolio / "accounts.json"
    accounts = _safe_json(accounts_path)
    if accounts_path.exists():
        _record_artifact(conn, root, accounts_path, "json", "portfolio", record_count=len(accounts))
    for account_id, account in accounts.items():
        _insert(
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
            stats,
        )
        _insert(
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
            stats,
        )

    positions_path = portfolio / "positions.json"
    positions = _safe_json(positions_path)
    if positions_path.exists():
        _record_artifact(conn, root, positions_path, "json", "portfolio")
    for account_id, account_positions in positions.items():
        if not isinstance(account_positions, dict):
            continue
        for symbol, position in account_positions.items():
            _insert(conn, "positions", _position_record(account_id, symbol, position), ["account_id", "symbol"], stats)

    _import_jsonl(conn, root, portfolio / "position_locks.jsonl", "position_locks", "lock_id", ["lock_id"], stats)
    _import_jsonl(conn, root, portfolio / "cash_ledger.jsonl", "cash_ledger", "cash_ledger_id", ["cash_ledger_id"], stats)
    _import_jsonl(conn, root, portfolio / "position_ledger.jsonl", "position_ledger", "position_ledger_id", ["position_ledger_id"], stats)
    _import_jsonl(conn, root, portfolio / "account_snapshots.jsonl", "account_snapshots", "snapshot_id", ["snapshot_id"], stats)
    _import_jsonl(conn, root, portfolio / "position_snapshots.jsonl", "position_snapshots", "snapshot_id", ["snapshot_id"], stats)
    _import_closed_positions(conn, root, portfolio / "closed_positions.jsonl", stats)


def _position_record(account_id: str, symbol: str, position: dict[str, Any]) -> dict[str, Any]:
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


def _import_jsonl(
    conn: sqlite3.Connection,
    root: Path,
    path: Path,
    table: str,
    id_field: str,
    conflict: list[str],
    stats: Counter[str],
) -> None:
    records = _safe_jsonl(path)
    if path.exists():
        _record_artifact(conn, root, path, "jsonl", table, record_count=len(records))
    for record in records:
        row = dict(record)
        if "payload_json" not in row:
            row["payload_json"] = json_text(record)
        if id_field not in row or not row.get(id_field):
            row[id_field] = _row_id(table, path, record)
        _insert(conn, table, row, conflict, stats)


def _import_closed_positions(conn: sqlite3.Connection, root: Path, path: Path, stats: Counter[str]) -> None:
    records = _safe_jsonl(path)
    if path.exists():
        _record_artifact(conn, root, path, "jsonl", "closed_positions", record_count=len(records))
    for record in records:
        row = {
            "closed_position_id": _row_id("closed", record.get("account_id"), record.get("symbol"), record.get("close_date"), record.get("created_at")),
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
        }
        _insert(conn, "closed_positions", row, ["closed_position_id"], stats)


def _import_paper_trading(conn: sqlite3.Connection, root: Path, stats: Counter[str]) -> None:
    paper = root / "paper_trading"
    _import_jsonl(conn, root, paper / "signals.jsonl", "paper_signals", "signal_id", ["signal_id"], stats)
    _import_jsonl(conn, root, paper / "orders.jsonl", "paper_orders", "order_id", ["order_id"], stats)
    _import_jsonl(conn, root, paper / "trades.jsonl", "paper_trades", "trade_id", ["trade_id"], stats)


def _import_decisions(conn: sqlite3.Connection, root: Path, stats: Counter[str]) -> None:
    for path in sorted((root / "strategy_snapshots").glob("strategy_snapshot_*.json")):
        snapshot = _safe_json(path)
        artifact_id = _record_artifact(
            conn,
            root,
            path,
            "json",
            "strategy_snapshot",
            metadata={"symbol": snapshot.get("symbol"), "trade_date": snapshot.get("trade_date")},
        )
        data_quality = snapshot.get("data_quality") or {}
        time_context = snapshot.get("time_context") or {}
        _insert(
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
                "artifact_id": artifact_id,
                "created_at": snapshot.get("decision_time"),
            },
            ["snapshot_id"],
            stats,
        )

    for path in sorted((root / "decision_results").glob("decision_result_*.json")):
        decision = _safe_json(path)
        artifact_id = _record_artifact(
            conn,
            root,
            path,
            "json",
            "decision_result",
            metadata={"symbol": decision.get("symbol"), "strategy_version": decision.get("strategy_version")},
        )
        time_context = decision.get("time_context") or {}
        _insert(
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
                "artifact_id": artifact_id,
                "created_at": decision.get("decision_time"),
            },
            ["decision_id"],
            stats,
        )


def _import_risk_and_allocation(conn: sqlite3.Connection, root: Path, stats: Counter[str]) -> None:
    _import_risk_checks(conn, root, root / "risk_control" / "risk_checks.jsonl", stats)
    construction = root / "portfolio_construction"
    _import_allocation_plans(conn, root, construction / "allocation_plans.jsonl", stats)
    _import_order_intents(conn, root, construction / "order_intents.jsonl", stats)


def _import_risk_checks(conn: sqlite3.Connection, root: Path, path: Path, stats: Counter[str]) -> None:
    records = _safe_jsonl(path)
    if path.exists():
        _record_artifact(conn, root, path, "jsonl", "risk_checks", record_count=len(records))
    for record in records:
        row = {
            "risk_check_id": record.get("risk_check_id") or _row_id("risk", path, record.get("decision_id"), record.get("created_at")),
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
        }
        _insert(conn, "risk_checks", row, ["risk_check_id"], stats)


def _import_allocation_plans(conn: sqlite3.Connection, root: Path, path: Path, stats: Counter[str]) -> None:
    records = _safe_jsonl(path)
    if path.exists():
        _record_artifact(conn, root, path, "jsonl", "allocation_plans", record_count=len(records))
    for record in records:
        row = dict(record)
        row["payload_json"] = json_text(record)
        _insert(conn, "allocation_plans", row, ["allocation_id"], stats)


def _import_order_intents(conn: sqlite3.Connection, root: Path, path: Path, stats: Counter[str]) -> None:
    records = _safe_jsonl(path)
    if path.exists():
        _record_artifact(conn, root, path, "jsonl", "order_intents", record_count=len(records))
    for record in records:
        row = dict(record)
        row["payload_json"] = json_text(record)
        _insert(conn, "order_intents", row, ["intent_id"], stats)


def _import_workflows(conn: sqlite3.Connection, root: Path, stats: Counter[str]) -> None:
    workflows = root / "workflows"
    runs = _safe_jsonl(workflows / "workflow_runs.jsonl")
    if (workflows / "workflow_runs.jsonl").exists():
        _record_artifact(conn, root, workflows / "workflow_runs.jsonl", "jsonl", "workflow_runs", record_count=len(runs))
    for run in runs:
        params = run.get("input_params_json") or {}
        row = {
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
        }
        _insert(conn, "workflow_runs", row, ["workflow_run_id"], stats)

    for path in sorted(workflows.glob("pre_market_plan_*.json")):
        plan = _safe_json(path)
        artifact_id = _record_artifact(conn, root, path, "json", "pre_market_plan")
        plan_id = _row_id("plan", path.stem)
        _insert(
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
                "artifact_id": artifact_id,
                "created_at": plan.get("created_at"),
            },
            ["plan_id"],
            stats,
        )

    for path in sorted(workflows.glob("trigger_price_list_*.json")):
        data = _safe_json(path)
        items = data.get("items") or []
        _record_artifact(conn, root, path, "json", "trigger_price_list", record_count=len(items))
        for item in items:
            triggers = item.get("trigger_prices") or {}
            trigger_item_id = _row_id(
                "tpi",
                data.get("account_id"),
                data.get("trade_date"),
                item.get("symbol"),
                item.get("task_type"),
                item.get("decision_id"),
            )
            row = {
                "trigger_item_id": trigger_item_id,
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
            }
            _insert(conn, "trigger_price_items", row, ["trigger_item_id"], stats)


def _import_intraday(conn: sqlite3.Connection, root: Path, stats: Counter[str]) -> None:
    intraday = root / "intraday"
    _import_intraday_scans(conn, root, intraday / "intraday_scans.jsonl", stats)
    _import_trigger_events(conn, root, intraday / "trigger_events.jsonl", stats)


def _import_intraday_scans(conn: sqlite3.Connection, root: Path, path: Path, stats: Counter[str]) -> None:
    records = _safe_jsonl(path)
    if path.exists():
        _record_artifact(conn, root, path, "jsonl", "intraday_scans", record_count=len(records))
    for record in records:
        row = dict(record)
        row["allow_non_trading"] = bool_int(record.get("allow_non_trading"))
        row["is_trading_day"] = bool_int(record.get("is_trading_day"))
        row["input_refs_json"] = json_text(record.get("input_refs") or record.get("input_refs_json") or {})
        row["created_at"] = record.get("created_at") or record.get("started_at")
        row["payload_json"] = json_text(record)
        row.pop("input_refs", None)
        row.pop("time_warnings", None)
        _insert(conn, "intraday_scans", row, ["scan_id"], stats)


def _import_trigger_events(conn: sqlite3.Connection, root: Path, path: Path, stats: Counter[str]) -> None:
    records = _safe_jsonl(path)
    if path.exists():
        _record_artifact(conn, root, path, "jsonl", "trigger_events", record_count=len(records))
    for record in records:
        row = dict(record)
        row["execution_allowed"] = bool_int(record.get("execution_allowed"))
        row["requires_human_confirm"] = bool_int(record.get("requires_human_confirm"))
        row["payload_json"] = json_text(record)
        _insert(conn, "trigger_events", row, ["trigger_event_id"], stats)


def _import_replays(conn: sqlite3.Connection, root: Path, stats: Counter[str]) -> None:
    replay_root = root / "replay"
    if not replay_root.exists():
        return
    for replay_dir in sorted(path for path in replay_root.iterdir() if path.is_dir()):
        run = _safe_json(replay_dir / "replay_run.json")
        config = _safe_json(replay_dir / "replay_config.json")
        replay_id = run.get("replay_id") or config.get("replay_id") or replay_dir.name
        payload = {**config, **run}
        _insert(
            conn,
            "replay_runs",
            {
                "replay_id": replay_id,
                "account_id": payload.get("account_id"),
                "symbols_json": json_text(payload.get("symbols") or []),
                "start_date": payload.get("start_date"),
                "end_date": payload.get("end_date"),
                "initial_cash": payload.get("initial_cash"),
                "replay_mode": payload.get("replay_mode"),
                "strategy_version": payload.get("strategy_version"),
                "execution_mode": payload.get("execution_mode"),
                "bar_dir": payload.get("bar_dir"),
                "cash_reserve_pct": payload.get("cash_reserve_pct"),
                "max_single_position_pct": payload.get("max_single_position_pct"),
                "max_daily_buy_amount": payload.get("max_daily_buy_amount"),
                "default_watch_cash": payload.get("default_watch_cash"),
                "started_at": payload.get("started_at"),
                "finished_at": payload.get("finished_at"),
                "status": payload.get("status"),
                "output_root": str(replay_dir),
                "report_path": payload.get("report_path"),
                "performance_path": payload.get("performance_path"),
                "error_code": payload.get("error_code"),
                "error_message": payload.get("error_message"),
                "created_at": payload.get("created_at"),
                "payload_json": json_text(payload),
            },
            ["replay_id"],
            stats,
        )
        _import_replay_daily_records(conn, root, replay_dir / "daily_replay_records.jsonl", replay_id, stats)
        _import_performance_metric(conn, root, replay_dir / "performance_metrics.json", "replay", replay_id, payload, stats)
        _import_portfolio(conn, replay_dir, stats)
        _import_paper_trading(conn, replay_dir, stats)
        _import_risk_checks(conn, replay_dir, replay_dir / "risk_checks.jsonl", stats)
        _import_allocation_plans(conn, replay_dir, replay_dir / "allocation_plans.jsonl", stats)
        _import_order_intents(conn, replay_dir, replay_dir / "order_intents.jsonl", stats)


def _import_replay_daily_records(conn: sqlite3.Connection, root: Path, path: Path, replay_id: str, stats: Counter[str]) -> None:
    records = _safe_jsonl(path)
    if path.exists():
        _record_artifact(conn, root, path, "jsonl", "replay_daily_records", record_count=len(records))
    for record in records:
        row = {
            "daily_record_id": _row_id("rd", replay_id, record.get("trade_date")),
            "replay_id": replay_id,
            "trade_date": record.get("trade_date"),
            "symbols_scanned": record.get("symbols_scanned"),
            "released_quantity": record.get("released_quantity"),
            "holding_decisions": record.get("holding_decisions"),
            "buy_decisions": record.get("buy_decisions"),
            "risk_checks": record.get("risk_checks"),
            "ready_intents": record.get("ready_intents"),
            "orders": record.get("orders"),
            "trades": record.get("trades"),
            "account_snapshot_id": record.get("account_snapshot_id"),
            "total_assets": record.get("total_assets"),
            "available_cash": record.get("available_cash"),
            "market_value": record.get("market_value"),
            "daily_pnl": record.get("daily_pnl"),
            "blocked_reasons_json": json_text(record.get("blocked_reasons") or []),
            "allocation_status": record.get("allocation_status"),
            "created_at": record.get("created_at"),
            "payload_json": json_text(record),
        }
        _insert(conn, "replay_daily_records", row, ["daily_record_id"], stats)


def _import_performance_metric(
    conn: sqlite3.Connection,
    root: Path,
    path: Path,
    source_type: str,
    source_id: str,
    source_payload: dict[str, Any],
    stats: Counter[str],
) -> None:
    metrics = _safe_json(path)
    if not metrics:
        return
    _record_artifact(conn, root, path, "json", "performance_metrics")
    _insert(
        conn,
        "performance_metrics",
        {
            "performance_metric_id": _row_id("perf", source_type, source_id),
            "source_type": source_type,
            "source_id": source_id,
            "account_id": source_payload.get("account_id"),
            "strategy_version": source_payload.get("strategy_version"),
            "start_date": metrics.get("start_date"),
            "end_date": metrics.get("end_date"),
            "initial_cash": metrics.get("initial_cash"),
            "final_assets": metrics.get("final_assets"),
            "total_return_pct": metrics.get("total_return_pct"),
            "annualized_return_pct": metrics.get("annualized_return_pct"),
            "max_drawdown_pct": metrics.get("max_drawdown_pct"),
            "max_drawdown_start": metrics.get("max_drawdown_start"),
            "max_drawdown_end": metrics.get("max_drawdown_end"),
            "trade_count": metrics.get("trade_count"),
            "buy_count": metrics.get("buy_count"),
            "sell_count": metrics.get("sell_count"),
            "win_rate": metrics.get("win_rate"),
            "profit_loss_ratio": metrics.get("profit_loss_ratio"),
            "average_win_pct": metrics.get("average_win_pct"),
            "average_loss_pct": metrics.get("average_loss_pct"),
            "largest_win_pct": metrics.get("largest_win_pct"),
            "largest_loss_pct": metrics.get("largest_loss_pct"),
            "average_holding_days": metrics.get("average_holding_days"),
            "cash_usage_pct": metrics.get("cash_usage_pct"),
            "turnover_rate": metrics.get("turnover_rate"),
            "benchmark_return_pct": metrics.get("benchmark_return_pct"),
            "excess_return_pct": metrics.get("excess_return_pct"),
            "payload_json": json_text(metrics),
            "created_at": now_iso(),
        },
        ["performance_metric_id"],
        stats,
    )


def _import_strategy_iterations(conn: sqlite3.Connection, root: Path, stats: Counter[str]) -> None:
    path = root / "strategy_iterations" / "strategy_tuning_records.jsonl"
    records = _safe_jsonl(path)
    if path.exists():
        _record_artifact(conn, root, path, "jsonl", "strategy_tuning_records", record_count=len(records))
    for record in records:
        row = {
            "iteration_id": record.get("iteration_id"),
            "created_at": record.get("created_at"),
            "source_type": record.get("source_type"),
            "source_id": record.get("source_id"),
            "source_path": record.get("source_path"),
            "strategy_version": record.get("strategy_version"),
            "previous_strategy_version": record.get("previous_strategy_version"),
            "account_id": record.get("account_id"),
            "symbols_json": json_text(record.get("symbols") or []),
            "period_start": record.get("period_start"),
            "period_end": record.get("period_end"),
            "metrics_json": json_text(record.get("metrics") or {}),
            "auto_issues_json": json_text(record.get("auto_issues") or []),
            "manual_issues_json": json_text(record.get("manual_issues") or []),
            "blocked_reason_counts_json": json_text(record.get("blocked_reason_counts") or {}),
            "worst_days_json": json_text(record.get("worst_days") or []),
            "error_case_count": record.get("error_case_count"),
            "closed_position_count": record.get("closed_position_count"),
            "hypothesis": record.get("hypothesis"),
            "rule_changes": record.get("rule_changes"),
            "risk_changes": record.get("risk_changes"),
            "position_changes": record.get("position_changes"),
            "next_action": record.get("next_action"),
            "conclusion": record.get("conclusion"),
            "tags_json": json_text(record.get("tags") or []),
            "notes": record.get("notes"),
            "payload_json": json_text(record),
        }
        _insert(conn, "strategy_tuning_records", row, ["iteration_id"], stats)


def _import_reports(conn: sqlite3.Connection, root: Path, stats: Counter[str]) -> None:
    reports_root = root / "reports"
    if reports_root.exists():
        paths = sorted(reports_root.glob("*.md"))
    else:
        paths = []
    replay_root = root / "replay"
    if replay_root.exists():
        paths.extend(sorted(replay_root.glob("*/replay_report.md")))
    for path in paths:
        artifact_id = _record_artifact(conn, root, path, "markdown", "reports")
        title = _report_title(path)
        report_type = _report_type(path)
        symbol = _symbol_from_report_name(path.name)
        trade_date = _date_from_name(path.name)
        _insert(
            conn,
            "reports",
            {
                "report_id": _row_id("rpt", _relative(root, path)),
                "report_type": report_type,
                "title": title,
                "account_id": None,
                "symbol": symbol,
                "trade_date": trade_date,
                "strategy_version": None,
                "source_type": None,
                "source_id": None,
                "artifact_id": artifact_id,
                "relative_path": _relative(root, path),
                "created_at": _file_created_at(path),
                "metadata_json": json_text({}),
            },
            ["report_id"],
            stats,
        )


def _report_title(path: Path) -> str | None:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                return line.strip().lstrip("#").strip()
    except UnicodeDecodeError:
        return None
    return None


def _report_type(path: Path) -> str:
    name = path.name
    for suffix in ["_report", "report"]:
        if suffix in name:
            return name.split(suffix, 1)[0].strip("_") or "report"
    return path.stem


def _symbol_from_report_name(name: str) -> str | None:
    parts = name.split("_")
    for part in parts:
        if part.isdigit() and len(part) == 6:
            return part
    return None


def _date_from_name(name: str) -> str | None:
    parts = name.replace(".md", "").split("_")
    for part in parts:
        if len(part) == 10 and part[4] == "-" and part[7] == "-":
            return part
    return None
