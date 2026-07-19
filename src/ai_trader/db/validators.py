"""Database summary, validation, and backup helpers."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..decision_utils import ensure_dir
from ..portfolio import compact_now
from .connection import connect, resolve_db_path
from .repositories import count_rows, table_exists


REQUIRED_TABLES = [
    "schema_migrations",
    "file_artifacts",
    "import_batches",
    "accounts",
    "account_states",
    "positions",
    "position_locks",
    "cash_ledger",
    "position_ledger",
    "closed_positions",
    "account_snapshots",
    "position_snapshots",
    "paper_signals",
    "paper_orders",
    "paper_trades",
    "strategy_snapshots",
    "decision_results",
    "analysis_snapshots",
    "strategy_runs",
    "strategy_evaluations",
    "strategy_aggregations",
    "strategy_analysis_sessions",
    "strategy_context_profiles",
    "strategy_context_revisions",
    "risk_checks",
    "allocation_plans",
    "order_intents",
    "workflow_runs",
    "pre_market_plans",
    "trigger_price_items",
    "intraday_scans",
    "trigger_events",
    "replay_runs",
    "replay_daily_records",
    "performance_metrics",
    "strategy_tuning_records",
    "reports",
    "ai_calls",
    "watchlist_tabs",
    "watchlist_items",
    "market_quotes",
    "market_data_batches",
    "market_data_batch_items",
    "daily_market_snapshots",
    "post_market_close_runs",
    "watchlist_screen_runs",
    "watchlist_screen_results",
]

SUMMARY_TABLES = [
    "accounts",
    "positions",
    "paper_signals",
    "paper_orders",
    "paper_trades",
    "decision_results",
    "analysis_snapshots",
    "strategy_runs",
    "strategy_evaluations",
    "strategy_aggregations",
    "strategy_analysis_sessions",
    "strategy_context_profiles",
    "strategy_context_revisions",
    "risk_checks",
    "allocation_plans",
    "order_intents",
    "workflow_runs",
    "trigger_events",
    "replay_runs",
    "performance_metrics",
    "strategy_tuning_records",
    "reports",
    "watchlist_tabs",
    "watchlist_items",
    "market_quotes",
    "market_data_batches",
    "market_data_batch_items",
    "daily_market_snapshots",
    "post_market_close_runs",
    "watchlist_screen_runs",
    "watchlist_screen_results",
]

PAYLOAD_TABLES = [
    "accounts",
    "account_states",
    "positions",
    "position_locks",
    "cash_ledger",
    "position_ledger",
    "closed_positions",
    "account_snapshots",
    "position_snapshots",
    "paper_signals",
    "paper_orders",
    "paper_trades",
    "strategy_snapshots",
    "decision_results",
    "analysis_snapshots",
    "strategy_runs",
    "strategy_evaluations",
    "strategy_aggregations",
    "strategy_analysis_sessions",
    "strategy_context_profiles",
    "strategy_context_revisions",
    "risk_checks",
    "allocation_plans",
    "order_intents",
    "workflow_runs",
    "pre_market_plans",
    "trigger_price_items",
    "intraday_scans",
    "trigger_events",
    "replay_runs",
    "replay_daily_records",
    "performance_metrics",
    "strategy_tuning_records",
    "watchlist_items",
    "market_data_batches",
    "market_data_batch_items",
    "daily_market_snapshots",
    "post_market_close_runs",
]


def _issue(
    issues: list[dict[str, Any]],
    *,
    code: str,
    severity: str,
    message: str,
    table: str | None = None,
    row_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    issues.append(
        {
            "code": code,
            "severity": severity,
            "message": message,
            "table": table,
            "row_id": row_id,
            "detail": detail or {},
        }
    )


def _has_tables(conn: sqlite3.Connection, *tables: str) -> bool:
    return all(table_exists(conn, table) for table in tables)


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def summary_database(output_root: Path, db_path: str | None = None) -> dict[str, Any]:
    resolved = resolve_db_path(output_root, db_path)
    if not resolved.exists():
        return {"db_path": str(resolved), "exists": False, "counts": {}, "accounts": [], "recent_reports": []}

    with connect(resolved) as conn:
        counts = {
            table: count_rows(conn, table)
            for table in SUMMARY_TABLES
            if table_exists(conn, table)
        }
        accounts = _summary_accounts(conn) if _has_tables(conn, "accounts", "account_states") else []
        active_positions = _summary_active_positions(conn) if table_exists(conn, "positions") else []
        recent_reports = _summary_recent_reports(conn) if table_exists(conn, "reports") else []
        recent_trades = _summary_recent_trades(conn) if table_exists(conn, "paper_trades") else []

    return {
        "db_path": str(resolved),
        "exists": True,
        "counts": counts,
        "accounts": accounts,
        "active_positions": active_positions,
        "recent_reports": recent_reports,
        "recent_trades": recent_trades,
    }


def _summary_accounts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            a.account_id,
            a.account_name,
            a.account_type,
            a.initial_cash,
            s.trade_date,
            s.available_cash,
            s.market_value,
            s.total_assets,
            s.equity_position_pct,
            s.cash_pct,
            s.updated_at
        FROM accounts a
        LEFT JOIN account_states s ON s.account_state_id = 'state_' || a.account_id || '_current'
        ORDER BY a.account_id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _summary_active_positions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT account_id, symbol, name, total_quantity, available_quantity, locked_quantity,
               avg_cost, market_price, market_value, position_pct, position_status, updated_at
        FROM positions
        WHERE COALESCE(position_status, 'ACTIVE') <> 'CLOSED'
          AND COALESCE(total_quantity, 0) > 0
        ORDER BY account_id, symbol
        LIMIT 50
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _summary_recent_reports(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT report_type, title, symbol, trade_date, relative_path, created_at
        FROM reports
        ORDER BY COALESCE(created_at, '') DESC
        LIMIT 10
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _summary_recent_trades(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT trade_id, account_id, symbol, side, quantity, fill_price, net_amount, trade_date, trade_time
        FROM paper_trades
        ORDER BY COALESCE(trade_time, trade_date, '') DESC
        LIMIT 10
        """
    ).fetchall()
    return [dict(row) for row in rows]


def validate_database(output_root: Path, db_path: str | None = None, *, json_sample_limit: int = 200) -> dict[str, Any]:
    resolved = resolve_db_path(output_root, db_path)
    issues: list[dict[str, Any]] = []
    if not resolved.exists():
        _issue(
            issues,
            code="DB_MISSING",
            severity="ERROR",
            message=f"database file not found: {resolved}",
        )
        return {"db_path": str(resolved), "ok": False, "issue_count": len(issues), "issues": issues}

    with connect(resolved) as conn:
        for table in REQUIRED_TABLES:
            if not table_exists(conn, table):
                _issue(
                    issues,
                    code="TABLE_MISSING",
                    severity="ERROR",
                    message=f"required table missing: {table}",
                    table=table,
                )
        if any(issue["severity"] == "ERROR" for issue in issues):
            return {"db_path": str(resolved), "ok": False, "issue_count": len(issues), "issues": issues}

        _validate_account_assets(conn, issues)
        _validate_position_market_value(conn, issues)
        _validate_position_locks(conn, issues)
        _validate_trade_references(conn, issues)
        _validate_decision_references(conn, issues)
        _validate_replay_references(conn, issues)
        _validate_intraday_references(conn, issues)
        _validate_import_batches(conn, issues)
        _validate_payload_json(conn, issues, json_sample_limit)

    return {"db_path": str(resolved), "ok": not issues, "issue_count": len(issues), "issues": issues}


def _validate_account_assets(conn: sqlite3.Connection, issues: list[dict[str, Any]]) -> None:
    rows = conn.execute(
        """
        SELECT account_state_id, account_id, available_cash, frozen_cash, market_value, total_assets
        FROM account_states
        WHERE total_assets IS NOT NULL
        """
    ).fetchall()
    for row in rows:
        available = _as_float(row["available_cash"]) or 0.0
        frozen = _as_float(row["frozen_cash"]) or 0.0
        market = _as_float(row["market_value"]) or 0.0
        total = _as_float(row["total_assets"]) or 0.0
        expected = available + frozen + market
        if abs(total - expected) > 0.05:
            _issue(
                issues,
                code="ACCOUNT_ASSET_MISMATCH",
                severity="ERROR",
                message="account total_assets does not equal cash plus market value",
                table="account_states",
                row_id=row["account_state_id"],
                detail={"account_id": row["account_id"], "expected": expected, "actual": total},
            )


def _validate_position_market_value(conn: sqlite3.Connection, issues: list[dict[str, Any]]) -> None:
    rows = conn.execute(
        """
        SELECT account_id, symbol, total_quantity, market_price, market_value
        FROM positions
        WHERE COALESCE(position_status, 'ACTIVE') <> 'CLOSED'
          AND total_quantity IS NOT NULL
          AND market_price IS NOT NULL
          AND market_value IS NOT NULL
        """
    ).fetchall()
    for row in rows:
        quantity = _as_float(row["total_quantity"]) or 0.0
        price = _as_float(row["market_price"]) or 0.0
        market_value = _as_float(row["market_value"]) or 0.0
        expected = quantity * price
        if abs(market_value - expected) > 0.05:
            _issue(
                issues,
                code="POSITION_MARKET_VALUE_MISMATCH",
                severity="WARNING",
                message="position market_value does not equal quantity times market_price",
                table="positions",
                row_id=f"{row['account_id']}:{row['symbol']}",
                detail={"expected": expected, "actual": market_value},
            )


def _validate_position_locks(conn: sqlite3.Connection, issues: list[dict[str, Any]]) -> None:
    rows = conn.execute(
        """
        SELECT
            p.account_id,
            p.symbol,
            COALESCE(p.locked_quantity, 0) AS locked_quantity,
            COALESCE(SUM(CASE WHEN l.status = 'OPEN' THEN l.remaining_locked_quantity ELSE 0 END), 0) AS lock_sum
        FROM positions p
        LEFT JOIN position_locks l ON l.account_id = p.account_id AND l.symbol = p.symbol
        WHERE COALESCE(p.position_status, 'ACTIVE') <> 'CLOSED'
        GROUP BY p.account_id, p.symbol, p.locked_quantity
        """
    ).fetchall()
    for row in rows:
        locked_quantity = int(row["locked_quantity"] or 0)
        lock_sum = int(row["lock_sum"] or 0)
        if locked_quantity != lock_sum:
            _issue(
                issues,
                code="POSITION_LOCK_MISMATCH",
                severity="ERROR",
                message="position locked quantity does not equal open lock sum",
                table="positions",
                row_id=f"{row['account_id']}:{row['symbol']}",
                detail={"locked_quantity": locked_quantity, "open_lock_sum": lock_sum},
            )


def _validate_trade_references(conn: sqlite3.Connection, issues: list[dict[str, Any]]) -> None:
    rows = conn.execute(
        """
        SELECT t.trade_id, t.order_id
        FROM paper_trades t
        LEFT JOIN paper_orders o ON o.order_id = t.order_id
        WHERE t.order_id IS NOT NULL AND o.order_id IS NULL
        """
    ).fetchall()
    for row in rows:
        _issue(
            issues,
            code="TRADE_ORDER_MISSING",
            severity="ERROR",
            message="paper trade references a missing paper order",
            table="paper_trades",
            row_id=row["trade_id"],
            detail={"order_id": row["order_id"]},
        )

    rows = conn.execute(
        """
        SELECT o.order_id, o.signal_id
        FROM paper_orders o
        LEFT JOIN paper_signals s ON s.signal_id = o.signal_id
        WHERE o.signal_id IS NOT NULL AND s.signal_id IS NULL
        """
    ).fetchall()
    for row in rows:
        _issue(
            issues,
            code="ORDER_SIGNAL_MISSING",
            severity="ERROR",
            message="paper order references a missing paper signal",
            table="paper_orders",
            row_id=row["order_id"],
            detail={"signal_id": row["signal_id"]},
        )


def _validate_decision_references(conn: sqlite3.Connection, issues: list[dict[str, Any]]) -> None:
    rows = conn.execute(
        """
        SELECT d.decision_id, d.snapshot_id
        FROM decision_results d
        LEFT JOIN strategy_snapshots s ON s.snapshot_id = d.snapshot_id
        WHERE d.snapshot_id IS NOT NULL AND s.snapshot_id IS NULL
        """
    ).fetchall()
    for row in rows:
        _issue(
            issues,
            code="DECISION_SNAPSHOT_MISSING",
            severity="WARNING",
            message="decision result references a missing strategy snapshot",
            table="decision_results",
            row_id=row["decision_id"],
            detail={"snapshot_id": row["snapshot_id"]},
        )

    rows = conn.execute(
        """
        SELECT sig.signal_id, sig.decision_id
        FROM paper_signals sig
        LEFT JOIN decision_results d ON d.decision_id = sig.decision_id
        WHERE sig.decision_id IS NOT NULL AND d.decision_id IS NULL
        """
    ).fetchall()
    for row in rows:
        _issue(
            issues,
            code="SIGNAL_DECISION_MISSING",
            severity="WARNING",
            message="paper signal references a missing decision result",
            table="paper_signals",
            row_id=row["signal_id"],
            detail={"decision_id": row["decision_id"]},
        )


def _validate_replay_references(conn: sqlite3.Connection, issues: list[dict[str, Any]]) -> None:
    rows = conn.execute(
        """
        SELECT r.daily_record_id, r.replay_id
        FROM replay_daily_records r
        LEFT JOIN replay_runs rr ON rr.replay_id = r.replay_id
        WHERE rr.replay_id IS NULL
        """
    ).fetchall()
    for row in rows:
        _issue(
            issues,
            code="REPLAY_DAILY_RUN_MISSING",
            severity="ERROR",
            message="replay daily record references a missing replay run",
            table="replay_daily_records",
            row_id=row["daily_record_id"],
            detail={"replay_id": row["replay_id"]},
        )

    rows = conn.execute(
        """
        SELECT p.performance_metric_id, p.source_id
        FROM performance_metrics p
        LEFT JOIN replay_runs rr ON rr.replay_id = p.source_id
        WHERE p.source_type = 'replay' AND rr.replay_id IS NULL
        """
    ).fetchall()
    for row in rows:
        _issue(
            issues,
            code="PERFORMANCE_REPLAY_MISSING",
            severity="ERROR",
            message="replay performance metrics reference a missing replay run",
            table="performance_metrics",
            row_id=row["performance_metric_id"],
            detail={"replay_id": row["source_id"]},
        )


def _validate_intraday_references(conn: sqlite3.Connection, issues: list[dict[str, Any]]) -> None:
    rows = conn.execute(
        """
        SELECT e.trigger_event_id, e.scan_id
        FROM trigger_events e
        LEFT JOIN intraday_scans s ON s.scan_id = e.scan_id
        WHERE e.scan_id IS NOT NULL AND s.scan_id IS NULL
        """
    ).fetchall()
    for row in rows:
        _issue(
            issues,
            code="TRIGGER_SCAN_MISSING",
            severity="WARNING",
            message="trigger event references a missing intraday scan",
            table="trigger_events",
            row_id=row["trigger_event_id"],
            detail={"scan_id": row["scan_id"]},
        )


def _validate_import_batches(conn: sqlite3.Connection, issues: list[dict[str, Any]]) -> None:
    rows = conn.execute(
        """
        SELECT batch_id, error_message
        FROM import_batches
        WHERE status = 'FAILED'
        ORDER BY started_at DESC
        LIMIT 20
        """
    ).fetchall()
    for row in rows:
        _issue(
            issues,
            code="IMPORT_BATCH_FAILED",
            severity="WARNING",
            message="previous import batch failed",
            table="import_batches",
            row_id=row["batch_id"],
            detail={"error_message": row["error_message"]},
        )


def _validate_payload_json(conn: sqlite3.Connection, issues: list[dict[str, Any]], sample_limit: int) -> None:
    for table in PAYLOAD_TABLES:
        if not table_exists(conn, table):
            continue
        if not _table_has_column(conn, table, "payload_json"):
            continue
        key_columns = _primary_key_columns(conn, table)
        selected_columns = key_columns + ["payload_json"]
        column_sql = ", ".join(selected_columns)
        rows = conn.execute(
            f"SELECT {column_sql} FROM {table} WHERE payload_json IS NOT NULL LIMIT ?",
            (sample_limit,),
        ).fetchall()
        for row in rows:
            try:
                json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                _issue(
                    issues,
                    code="PAYLOAD_JSON_INVALID",
                    severity="WARNING",
                    message="payload_json is not valid JSON",
                    table=table,
                    row_id=_row_identifier(row, key_columns),
                )


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def _primary_key_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    ordered = sorted((row["pk"], row["name"]) for row in rows if row["pk"])
    return [name for _, name in ordered]


def _row_identifier(row: sqlite3.Row, key_columns: list[str]) -> str | None:
    if not key_columns:
        return None
    return ":".join(str(row[column]) for column in key_columns)


def backup_database(output_root: Path, db_path: str | None = None, backup_dir: str | None = None) -> dict[str, Any]:
    resolved = resolve_db_path(output_root, db_path)
    if not resolved.exists():
        raise FileNotFoundError(f"database file not found: {resolved}")

    target_dir = Path(backup_dir) if backup_dir else output_root / "backups"
    ensure_dir(target_dir)
    target = target_dir / f"ai_trader_{compact_now()}.sqlite3"
    with sqlite3.connect(str(resolved)) as source, sqlite3.connect(str(target)) as destination:
        source.backup(destination)
    return {"source": str(resolved), "backup_path": str(target)}
