"""Lightweight reconciliation between current files and SQLite indexes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..file_store import load_json, read_jsonl
from .connection import connect, resolve_db_path
from .repositories import count_rows, table_exists
from .writers import relative_path, row_id


def reconcile_database(output_root: Path, db_path: str | None = None) -> dict[str, Any]:
    resolved = resolve_db_path(output_root, db_path)
    issues: list[dict[str, Any]] = []
    counts: dict[str, dict[str, int]] = {}
    if not resolved.exists():
        return {
            "db_path": str(resolved),
            "exists": False,
            "ok": False,
            "issue_count": 1,
            "issues": [
                {
                    "severity": "ERROR",
                    "code": "DB_MISSING",
                    "message": "SQLite database does not exist. Run `python .\\database.py init` first.",
                }
            ],
            "counts": counts,
        }

    with connect(resolved) as conn:
        _check_accounts(conn, output_root, issues, counts)
        _check_positions(conn, output_root, issues, counts)
        _check_jsonl_records(
            conn,
            output_root / "portfolio" / "position_locks.jsonl",
            "position_locks",
            "lock_id",
            issues,
            counts,
        )
        _check_jsonl_records(
            conn,
            output_root / "portfolio" / "cash_ledger.jsonl",
            "cash_ledger",
            "cash_ledger_id",
            issues,
            counts,
        )
        _check_jsonl_records(
            conn,
            output_root / "portfolio" / "position_ledger.jsonl",
            "position_ledger",
            "position_ledger_id",
            issues,
            counts,
        )
        _check_jsonl_records(
            conn,
            output_root / "portfolio" / "closed_positions.jsonl",
            "closed_positions",
            "closed_position_id",
            issues,
            counts,
            id_builder=lambda record: row_id(
                "closed",
                record.get("account_id"),
                record.get("symbol"),
                record.get("close_date"),
                record.get("created_at"),
            ),
        )
        _check_jsonl_records(
            conn,
            output_root / "portfolio" / "account_snapshots.jsonl",
            "account_snapshots",
            "snapshot_id",
            issues,
            counts,
        )
        _check_jsonl_records(
            conn,
            output_root / "portfolio" / "position_snapshots.jsonl",
            "position_snapshots",
            "snapshot_id",
            issues,
            counts,
        )
        _check_jsonl_records(
            conn,
            output_root / "paper_trading" / "signals.jsonl",
            "paper_signals",
            "signal_id",
            issues,
            counts,
        )
        _check_jsonl_records(
            conn,
            output_root / "paper_trading" / "orders.jsonl",
            "paper_orders",
            "order_id",
            issues,
            counts,
        )
        _check_jsonl_records(
            conn,
            output_root / "paper_trading" / "trades.jsonl",
            "paper_trades",
            "trade_id",
            issues,
            counts,
        )
        _check_json_records(
            conn,
            output_root,
            "strategy_snapshots",
            "strategy_snapshots",
            "strategy_snapshot_*.json",
            "snapshot_id",
            issues,
            counts,
        )
        _check_json_records(
            conn,
            output_root,
            "decision_results",
            "decision_results",
            "decision_result_*.json",
            "decision_id",
            issues,
            counts,
        )
        _check_jsonl_records(
            conn,
            output_root / "risk_control" / "risk_checks.jsonl",
            "risk_checks",
            "risk_check_id",
            issues,
            counts,
        )
        _check_jsonl_records(
            conn,
            output_root / "portfolio_construction" / "allocation_plans.jsonl",
            "allocation_plans",
            "allocation_id",
            issues,
            counts,
        )
        _check_jsonl_records(
            conn,
            output_root / "portfolio_construction" / "order_intents.jsonl",
            "order_intents",
            "intent_id",
            issues,
            counts,
        )
        _check_jsonl_records(
            conn,
            output_root / "workflows" / "workflow_runs.jsonl",
            "workflow_runs",
            "workflow_run_id",
            issues,
            counts,
        )
        _check_pre_market_plans(conn, output_root, issues, counts)
        _check_trigger_price_items(conn, output_root, issues, counts)
        _check_jsonl_records(
            conn,
            output_root / "intraday" / "intraday_scans.jsonl",
            "intraday_scans",
            "scan_id",
            issues,
            counts,
        )
        _check_jsonl_records(
            conn,
            output_root / "intraday" / "trigger_events.jsonl",
            "trigger_events",
            "trigger_event_id",
            issues,
            counts,
        )
        _check_reports(conn, output_root, issues, counts)

    error_count = sum(1 for issue in issues if issue["severity"] == "ERROR")
    return {
        "db_path": str(resolved),
        "exists": True,
        "ok": error_count == 0,
        "issue_count": len(issues),
        "error_count": error_count,
        "issues": issues,
        "counts": counts,
    }


def _check_json_records(
    conn: Any,
    output_root: Path,
    directory_name: str,
    table: str,
    pattern: str,
    id_column: str,
    issues: list[dict[str, Any]],
    counts: dict[str, dict[str, int]],
) -> None:
    paths = sorted((output_root / directory_name).glob(pattern))
    db_count = count_rows(conn, table) if table_exists(conn, table) else 0
    counts[table] = {"files": len(paths), "db_rows": db_count}
    if not table_exists(conn, table):
        _issue(issues, "ERROR", "TABLE_MISSING", f"table `{table}` is missing", table=table)
        return

    for path in paths:
        record = load_json(path, {})
        record_id = record.get(id_column)
        if not record_id:
            _issue(
                issues,
                "ERROR",
                "FILE_ID_MISSING",
                f"{path} has no `{id_column}`",
                table=table,
                path=path,
            )
            continue
        row = conn.execute(f"SELECT 1 FROM {table} WHERE {id_column}=?", (record_id,)).fetchone()
        if row is None:
            _issue(
                issues,
                "ERROR",
                "FILE_NOT_INDEXED",
                f"{path} is not indexed in `{table}`",
                table=table,
                row_id=record_id,
                path=path,
            )

    if db_count < len(paths):
        _issue(
            issues,
            "WARNING",
            "DB_COUNT_LT_FILE_COUNT",
            f"`{table}` has fewer rows than current files",
            table=table,
            detail={"files": len(paths), "db_rows": db_count},
        )


def _check_accounts(
    conn: Any,
    output_root: Path,
    issues: list[dict[str, Any]],
    counts: dict[str, dict[str, int]],
) -> None:
    accounts = load_json(output_root / "portfolio" / "accounts.json", {})
    db_count = count_rows(conn, "accounts") if table_exists(conn, "accounts") else 0
    state_count = count_rows(conn, "account_states") if table_exists(conn, "account_states") else 0
    counts["accounts"] = {"files": len(accounts), "db_rows": db_count}
    counts["account_states"] = {"files": len(accounts), "db_rows": state_count}
    for table in ["accounts", "account_states"]:
        if not table_exists(conn, table):
            _issue(issues, "ERROR", "TABLE_MISSING", f"table `{table}` is missing", table=table)
            return
    for account_id in accounts:
        if conn.execute("SELECT 1 FROM accounts WHERE account_id=?", (account_id,)).fetchone() is None:
            _issue(issues, "ERROR", "FILE_NOT_INDEXED", "account is not indexed", table="accounts", row_id=account_id)
        state_id = f"state_{account_id}_current"
        if conn.execute("SELECT 1 FROM account_states WHERE account_state_id=?", (state_id,)).fetchone() is None:
            _issue(issues, "ERROR", "FILE_NOT_INDEXED", "account state is not indexed", table="account_states", row_id=state_id)


def _check_positions(
    conn: Any,
    output_root: Path,
    issues: list[dict[str, Any]],
    counts: dict[str, dict[str, int]],
) -> None:
    positions = load_json(output_root / "portfolio" / "positions.json", {})
    expected: list[tuple[str, str]] = []
    for account_id, account_positions in positions.items():
        if not isinstance(account_positions, dict):
            continue
        for symbol, position in account_positions.items():
            expected.append((account_id, position.get("symbol") or symbol))
    db_count = count_rows(conn, "positions") if table_exists(conn, "positions") else 0
    counts["positions"] = {"files": len(expected), "db_rows": db_count}
    if not table_exists(conn, "positions"):
        _issue(issues, "ERROR", "TABLE_MISSING", "table `positions` is missing", table="positions")
        return
    for account_id, symbol in expected:
        row = conn.execute("SELECT 1 FROM positions WHERE account_id=? AND symbol=?", (account_id, symbol)).fetchone()
        if row is None:
            _issue(
                issues,
                "ERROR",
                "FILE_NOT_INDEXED",
                "position is not indexed",
                table="positions",
                row_id=f"{account_id}:{symbol}",
            )


def _check_jsonl_records(
    conn: Any,
    path: Path,
    table: str,
    id_column: str,
    issues: list[dict[str, Any]],
    counts: dict[str, dict[str, int]],
    id_builder: Any | None = None,
) -> None:
    records = read_jsonl(path) if path.exists() else []
    db_count = count_rows(conn, table) if table_exists(conn, table) else 0
    counts[table] = {"files": len(records), "db_rows": db_count}
    if not table_exists(conn, table):
        _issue(issues, "ERROR", "TABLE_MISSING", f"table `{table}` is missing", table=table)
        return

    for record in records:
        record_id = record.get(id_column) or (id_builder(record) if id_builder else None)
        if not record_id:
            _issue(
                issues,
                "ERROR",
                "FILE_ID_MISSING",
                f"{path} has a record without `{id_column}`",
                table=table,
                path=path,
            )
            continue
        row = conn.execute(f"SELECT 1 FROM {table} WHERE {id_column}=?", (record_id,)).fetchone()
        if row is None:
            _issue(
                issues,
                "ERROR",
                "FILE_NOT_INDEXED",
                f"{path} record is not indexed in `{table}`",
                table=table,
                row_id=record_id,
                path=path,
            )

    if db_count < len(records):
        _issue(
            issues,
            "WARNING",
            "DB_COUNT_LT_FILE_COUNT",
            f"`{table}` has fewer rows than current JSONL records",
            table=table,
            detail={"files": len(records), "db_rows": db_count},
        )


def _check_pre_market_plans(
    conn: Any,
    output_root: Path,
    issues: list[dict[str, Any]],
    counts: dict[str, dict[str, int]],
) -> None:
    table = "pre_market_plans"
    paths = sorted((output_root / "workflows").glob("pre_market_plan_*.json"))
    db_count = count_rows(conn, table) if table_exists(conn, table) else 0
    counts[table] = {"files": len(paths), "db_rows": db_count}
    if not table_exists(conn, table):
        _issue(issues, "ERROR", "TABLE_MISSING", f"table `{table}` is missing", table=table)
        return
    for path in paths:
        plan_id = row_id("plan", path.stem)
        row = conn.execute("SELECT 1 FROM pre_market_plans WHERE plan_id=?", (plan_id,)).fetchone()
        if row is None:
            _issue(
                issues,
                "ERROR",
                "FILE_NOT_INDEXED",
                f"{path} is not indexed in `pre_market_plans`",
                table=table,
                row_id=plan_id,
                path=path,
            )


def _check_trigger_price_items(
    conn: Any,
    output_root: Path,
    issues: list[dict[str, Any]],
    counts: dict[str, dict[str, int]],
) -> None:
    table = "trigger_price_items"
    paths = sorted((output_root / "workflows").glob("trigger_price_list_*.json"))
    expected_ids: list[tuple[str, Path]] = []
    for path in paths:
        data = load_json(path, {})
        for item in data.get("items") or []:
            expected_ids.append(
                (
                    row_id(
                        "tpi",
                        data.get("account_id"),
                        data.get("trade_date"),
                        item.get("symbol"),
                        item.get("task_type"),
                        item.get("decision_id"),
                    ),
                    path,
                )
            )
    db_count = count_rows(conn, table) if table_exists(conn, table) else 0
    counts[table] = {"files": len(expected_ids), "db_rows": db_count}
    if not table_exists(conn, table):
        _issue(issues, "ERROR", "TABLE_MISSING", f"table `{table}` is missing", table=table)
        return
    for trigger_item_id, path in expected_ids:
        row = conn.execute("SELECT 1 FROM trigger_price_items WHERE trigger_item_id=?", (trigger_item_id,)).fetchone()
        if row is None:
            _issue(
                issues,
                "ERROR",
                "FILE_NOT_INDEXED",
                f"{path} item is not indexed in `trigger_price_items`",
                table=table,
                row_id=trigger_item_id,
                path=path,
            )


def _check_reports(
    conn: Any,
    output_root: Path,
    issues: list[dict[str, Any]],
    counts: dict[str, dict[str, int]],
) -> None:
    paths = sorted((output_root / "reports").glob("*.md"))
    replay_root = output_root / "replay"
    if replay_root.exists():
        paths.extend(sorted(replay_root.glob("*/replay_report.md")))

    table = "reports"
    db_count = count_rows(conn, table) if table_exists(conn, table) else 0
    counts[table] = {"files": len(paths), "db_rows": db_count}
    if not table_exists(conn, table):
        _issue(issues, "ERROR", "TABLE_MISSING", "table `reports` is missing", table=table)
        return

    for path in paths:
        report_id = row_id("rpt", relative_path(output_root, path))
        row = conn.execute("SELECT 1 FROM reports WHERE report_id=?", (report_id,)).fetchone()
        if row is None:
            _issue(
                issues,
                "ERROR",
                "FILE_NOT_INDEXED",
                f"{path} is not indexed in `reports`",
                table=table,
                row_id=report_id,
                path=path,
            )

    if db_count < len(paths):
        _issue(
            issues,
            "WARNING",
            "DB_COUNT_LT_FILE_COUNT",
            "`reports` has fewer rows than current files",
            table=table,
            detail={"files": len(paths), "db_rows": db_count},
        )


def _issue(
    issues: list[dict[str, Any]],
    severity: str,
    code: str,
    message: str,
    *,
    table: str | None = None,
    row_id: str | None = None,
    path: Path | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    issues.append(
        {
            "severity": severity,
            "code": code,
            "message": message,
            "table": table,
            "row_id": row_id,
            "path": str(path) if path else None,
            "detail": detail or {},
        }
    )
