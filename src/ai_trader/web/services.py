"""Read-only data services for the local dashboard."""

from __future__ import annotations

import json
import platform
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from ..db.connection import connect
from ..db.consistency import reconcile_database
from ..db.repositories import count_rows, table_exists
from ..db.validators import REQUIRED_TABLES, summary_database, validate_database
from ..file_store import read_jsonl
from ..portfolio import now_iso
from ..timekeeper import _tzinfo
from .settings import DashboardSettings


def template_context(settings: DashboardSettings, active: str, message: str | None = None, error: str | None = None) -> dict[str, Any]:
    return {
        "active": active,
        "settings": settings,
        "topbar": topbar(settings),
        "message": message,
        "error": error,
    }


def topbar(settings: DashboardSettings) -> dict[str, Any]:
    db_exists = settings.db_path.exists()
    latest_import = latest_import_batch(settings)
    sync = sync_status(settings)
    return {
        "now": now_iso(),
        "calendar_date": datetime.now(_tzinfo()).date().isoformat(),
        "db_exists": db_exists,
        "db_path": str(settings.db_path),
        "latest_import": latest_import,
        "db_status": "OK" if db_exists else "MISSING",
        "import_status": latest_import.get("status") if latest_import else "MISSING",
        "sync_status": sync.get("status"),
        "sync_issue_count": sync.get("issue_count"),
    }


def db_ready(settings: DashboardSettings) -> bool:
    return settings.db_path.exists()


def connect_if_ready(settings: DashboardSettings) -> sqlite3.Connection | None:
    if not db_ready(settings):
        return None
    return connect(settings.db_path)


def rows(settings: DashboardSettings, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn = connect_if_ready(settings)
    if conn is None:
        return []
    with conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def one(settings: DashboardSettings, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    conn = connect_if_ready(settings)
    if conn is None:
        return None
    with conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def safe_rows(settings: DashboardSettings, table: str, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn = connect_if_ready(settings)
    if conn is None:
        return []
    with conn:
        if not table_exists(conn, table):
            return []
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def safe_count(settings: DashboardSettings, table: str) -> int:
    conn = connect_if_ready(settings)
    if conn is None:
        return 0
    with conn:
        if not table_exists(conn, table):
            return 0
        return count_rows(conn, table)


def latest_import_batch(settings: DashboardSettings) -> dict[str, Any] | None:
    items = safe_rows(
        settings,
        "import_batches",
        """
        SELECT batch_id, source_type, started_at, finished_at, status, record_count, error_message
        FROM import_batches
        ORDER BY COALESCE(started_at, created_at, '') DESC
        LIMIT 1
        """,
    )
    return items[0] if items else None


def sync_status(settings: DashboardSettings) -> dict[str, Any]:
    try:
        result = reconcile_database(settings.output_root, str(settings.db_path))
    except Exception as exc:
        return {
            "db_path": str(settings.db_path),
            "exists": settings.db_path.exists(),
            "ok": False,
            "status": "ERROR",
            "issue_count": 1,
            "error_count": 1,
            "issues": [
                {
                    "severity": "ERROR",
                    "code": "RECONCILE_FAILED",
                    "message": str(exc),
                    "table": None,
                    "row_id": None,
                    "path": None,
                    "detail": {},
                }
            ],
            "counts": {},
        }
    if not result.get("exists"):
        result["status"] = "MISSING"
    elif result.get("ok"):
        result["status"] = "OK"
    else:
        result["status"] = "ERROR"
    errors = sync_errors(settings, limit=5)
    result["sync_error_log_count"] = sync_error_count(settings)
    result["recent_sync_errors"] = errors
    return result


def sync_error_count(settings: DashboardSettings) -> int:
    path = settings.output_root / "logs" / "db_sync_errors.jsonl"
    return len(read_jsonl(path)) if path.exists() else 0


def sync_errors(settings: DashboardSettings, limit: int = 20) -> list[dict[str, Any]]:
    path = settings.output_root / "logs" / "db_sync_errors.jsonl"
    records = read_jsonl(path) if path.exists() else []
    return list(reversed(records[-limit:]))


def dashboard(settings: DashboardSettings) -> dict[str, Any]:
    summary = summary_database(settings.output_root, str(settings.db_path))
    counts = summary.get("counts") or {}
    accounts = summary.get("accounts") or []
    active_positions = summary.get("active_positions") or []
    recent_reports = reports(settings, limit=10)
    recent_decisions = decisions(settings, limit=8)
    recent_risks = risk_checks(settings, limit=8)
    metrics = build_metrics(summary, recent_risks)
    alerts = build_alerts(settings, summary, recent_risks)
    return {
        "summary": summary,
        "counts": counts,
        "accounts": accounts,
        "active_positions": active_positions,
        "recent_reports": recent_reports,
        "recent_decisions": recent_decisions,
        "recent_risks": recent_risks,
        "metrics": metrics,
        "alerts": alerts,
    }


def build_metrics(summary: dict[str, Any], recent_risks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    accounts = summary.get("accounts") or []
    total_assets = sum(float(item.get("total_assets") or 0) for item in accounts)
    cash = sum(float(item.get("available_cash") or 0) for item in accounts)
    market_value = sum(float(item.get("market_value") or 0) for item in accounts)
    position_pct = (market_value / total_assets * 100) if total_assets else None
    counts = summary.get("counts") or {}
    blocked_risks = sum(1 for item in recent_risks if str(item.get("risk_status") or "").upper() in {"REJECTED", "BLOCKED"})
    return [
        {"label": "总资产", "value": total_assets, "kind": "money"},
        {"label": "可用现金", "value": cash, "kind": "money"},
        {"label": "持仓市值", "value": market_value, "kind": "money"},
        {"label": "仓位比例", "value": position_pct, "kind": "pct"},
        {"label": "最近决策", "value": counts.get("decision_results", 0), "kind": "number"},
        {"label": "风控拒绝", "value": blocked_risks, "kind": "number"},
    ]


def build_alerts(settings: DashboardSettings, summary: dict[str, Any], recent_risks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    sync = sync_status(settings)
    if not summary.get("exists"):
        alerts.append({"severity": "ERROR", "message": "数据库不存在，先执行导入。"})
    elif not sync.get("ok"):
        alerts.append({"severity": "ERROR", "message": f"数据库同步对账异常：{sync.get('issue_count')} 个问题。"})
    if summary.get("exists") and not latest_import_batch(settings):
        alerts.append({"severity": "WARNING", "message": "数据库存在，但还没有导入批次记录。"})
    for risk in recent_risks:
        if str(risk.get("risk_status") or "").upper() in {"REJECTED", "BLOCKED"}:
            alerts.append({"severity": "WARNING", "message": f"{risk.get('symbol') or '-'} 被风控拦截：{risk.get('risk_level') or risk.get('risk_status')}"})
    if not alerts:
        alerts.append({"severity": "OK", "message": "当前没有发现需要立即处理的数据库状态问题。"})
    return alerts[:8]


def accounts(settings: DashboardSettings) -> list[dict[str, Any]]:
    return safe_rows(
        settings,
        "accounts",
        """
        SELECT
            a.account_id, a.account_name, a.account_type, a.initial_cash,
            a.cash_reserve_pct, a.max_single_position_pct, a.max_daily_buy_amount,
            s.trade_date, s.available_cash, s.frozen_cash, s.market_value, s.total_assets,
            s.equity_position_pct, s.cash_pct, s.today_buy_used, s.today_sell_amount,
            s.updated_at
        FROM accounts a
        LEFT JOIN account_states s ON s.account_state_id = 'state_' || a.account_id || '_current'
        ORDER BY a.account_id
        """,
    )


def positions(settings: DashboardSettings) -> list[dict[str, Any]]:
    return safe_rows(
        settings,
        "positions",
        """
        SELECT account_id, symbol, name, asset_type, total_quantity, available_quantity,
               locked_quantity, avg_cost, market_price, market_value, unrealized_pnl,
               unrealized_pnl_pct, position_pct, first_buy_date, last_trade_date,
               buy_logic, invalidation_point, stop_loss_price, planned_position_pct,
               position_status, updated_at
        FROM positions
        ORDER BY account_id, COALESCE(position_status, 'ACTIVE'), symbol
        """,
    )


def decisions(
    settings: DashboardSettings,
    *,
    symbol: str | None = None,
    action: str | None = None,
    task_type: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    where: list[str] = []
    params: list[Any] = []
    if symbol:
        where.append("symbol = ?")
        params.append(symbol)
    if action:
        where.append("final_action = ?")
        params.append(action)
    if task_type:
        where.append("task_type = ?")
        params.append(task_type)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(limit)
    return safe_rows(
        settings,
        "decision_results",
        f"""
        SELECT decision_id, snapshot_id, symbol, name, task_type, trade_date,
               decision_time, strategy_version, schema_version, final_action,
               confidence, action_reason, human_review_required, artifact_id, created_at
        FROM decision_results
        {where_sql}
        ORDER BY COALESCE(decision_time, created_at, '') DESC
        LIMIT ?
        """,
        tuple(params),
    )


def risk_checks(settings: DashboardSettings, limit: int = 100) -> list[dict[str, Any]]:
    return safe_rows(
        settings,
        "risk_checks",
        """
        SELECT risk_check_id, account_id, decision_id, symbol, name, trade_date,
               risk_status, risk_level, allowed_action, original_action,
               max_cash_amount, max_quantity, reference_price,
               human_review_required, execution_allowed, created_at
        FROM risk_checks
        ORDER BY COALESCE(created_at, trade_date, '') DESC
        LIMIT ?
        """,
        (limit,),
    )


def allocation_plans(settings: DashboardSettings, limit: int = 100) -> list[dict[str, Any]]:
    return safe_rows(
        settings,
        "allocation_plans",
        """
        SELECT allocation_id, account_id, trade_date, strategy_version, cash_before,
               cash_reserved, buy_budget, planned_buy_amount, planned_position_count,
               candidate_count, rejected_count, deferred_count, record_only_count,
               status, created_at
        FROM allocation_plans
        ORDER BY COALESCE(created_at, trade_date, '') DESC
        LIMIT ?
        """,
        (limit,),
    )


def order_intents(settings: DashboardSettings, limit: int = 100) -> list[dict[str, Any]]:
    return safe_rows(
        settings,
        "order_intents",
        """
        SELECT intent_id, allocation_id, account_id, decision_id, symbol, name, side,
               rank, score, planned_cash_amount, planned_quantity, reference_price,
               reason, status, created_at
        FROM order_intents
        ORDER BY COALESCE(created_at, '') DESC
        LIMIT ?
        """,
        (limit,),
    )


def workflows(settings: DashboardSettings, limit: int = 100) -> dict[str, list[dict[str, Any]]]:
    return {
        "runs": safe_rows(
            settings,
            "workflow_runs",
            """
            SELECT workflow_run_id, workflow_type, account_id, trade_date, calendar_date,
                   session_name, is_trading_day, effective_data_cutoff, started_at,
                   finished_at, status, error_code, error_message
            FROM workflow_runs
            ORDER BY COALESCE(started_at, created_at, '') DESC
            LIMIT ?
            """,
            (limit,),
        ),
        "scans": safe_rows(
            settings,
            "intraday_scans",
            """
            SELECT scan_id, account_id, trade_date, calendar_date, session_name,
                   is_trading_day, status, symbols_scanned, trigger_count, blocked_count,
                   duplicate_count, report_path, started_at, finished_at, error_message
            FROM intraday_scans
            ORDER BY COALESCE(started_at, created_at, '') DESC
            LIMIT ?
            """,
            (limit,),
        ),
        "events": safe_rows(
            settings,
            "trigger_events",
            """
            SELECT trigger_event_id, account_id, trade_date, scan_id, symbol, name,
                   event_type, trigger_price, current_price, severity, suggested_action,
                   execution_allowed, requires_human_confirm, risk_status, blocked_reason,
                   created_at
            FROM trigger_events
            ORDER BY COALESCE(created_at, trade_date, '') DESC
            LIMIT ?
            """,
            (limit,),
        ),
    }


def replays(settings: DashboardSettings, limit: int = 100) -> list[dict[str, Any]]:
    return safe_rows(
        settings,
        "replay_runs",
        """
        SELECT r.replay_id, r.account_id, r.symbols_json, r.start_date, r.end_date,
               r.initial_cash, r.replay_mode, r.strategy_version, r.execution_mode,
               r.status, r.output_root, r.report_path, r.created_at,
               p.final_assets, p.total_return_pct, p.max_drawdown_pct, p.trade_count,
               p.win_rate, p.profit_loss_ratio
        FROM replay_runs r
        LEFT JOIN performance_metrics p ON p.source_type = 'replay' AND p.source_id = r.replay_id
        ORDER BY COALESCE(r.started_at, r.created_at, '') DESC
        LIMIT ?
        """,
        (limit,),
    )


def strategy_iterations(settings: DashboardSettings, limit: int = 100) -> list[dict[str, Any]]:
    items = safe_rows(
        settings,
        "strategy_tuning_records",
        """
        SELECT iteration_id, created_at, source_type, source_id, strategy_version,
               previous_strategy_version, account_id, symbols_json, period_start,
               period_end, metrics_json, auto_issues_json, manual_issues_json,
               hypothesis, rule_changes, risk_changes, position_changes,
               next_action, conclusion, tags_json, notes
        FROM strategy_tuning_records
        ORDER BY COALESCE(created_at, '') DESC
        LIMIT ?
        """,
        (limit,),
    )
    for item in items:
        item["metrics"] = parse_json(item.get("metrics_json"), {})
        item["auto_issues"] = parse_json(item.get("auto_issues_json"), [])
        item["manual_issues"] = parse_json(item.get("manual_issues_json"), [])
    return items


def reports(settings: DashboardSettings, limit: int = 200) -> list[dict[str, Any]]:
    return safe_rows(
        settings,
        "reports",
        """
        SELECT report_id, report_type, title, account_id, symbol, trade_date,
               strategy_version, source_type, source_id, relative_path, created_at
        FROM reports
        ORDER BY COALESCE(created_at, '') DESC
        LIMIT ?
        """,
        (limit,),
    )


def report_detail(settings: DashboardSettings, report_id: str) -> dict[str, Any] | None:
    report = one(
        settings,
        """
        SELECT report_id, report_type, title, account_id, symbol, trade_date,
               strategy_version, source_type, source_id, relative_path, created_at
        FROM reports
        WHERE report_id = ?
        """,
        (report_id,),
    )
    if not report:
        return None
    path = safe_output_path(settings.output_root, report.get("relative_path"))
    report["absolute_path"] = str(path) if path else None
    report["content"] = read_text(path) if path else "报告路径无效。"
    return report


def data_health(settings: DashboardSettings) -> dict[str, Any]:
    result = validate_database(settings.output_root, str(settings.db_path))
    sync = sync_status(settings)
    batches = safe_rows(
        settings,
        "import_batches",
        """
        SELECT batch_id, source_type, source_path, started_at, finished_at, status,
               record_count, success_count, failed_count, error_message
        FROM import_batches
        ORDER BY COALESCE(started_at, created_at, '') DESC
        LIMIT 20
        """,
    )
    table_counts = {}
    conn = connect_if_ready(settings)
    if conn is not None:
        with conn:
            for table in REQUIRED_TABLES:
                table_counts[table] = count_rows(conn, table) if table_exists(conn, table) else None
    return {
        "validation": result,
        "sync": sync,
        "sync_errors": sync_errors(settings),
        "batches": batches,
        "table_counts": table_counts,
    }


def settings_info(settings: DashboardSettings) -> dict[str, Any]:
    return {
        "output_root": str(settings.output_root.resolve()),
        "db_path": str(settings.db_path.resolve()),
        "db_exists": settings.db_path.exists(),
        "host": settings.host,
        "port": settings.port,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "project_root": str(Path.cwd()),
        "timezone": str(_tzinfo()),
    }


def parse_json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def safe_output_path(output_root: Path, relative_path: Any) -> Path | None:
    if not relative_path:
        return None
    root = output_root.resolve()
    candidate = (root / str(relative_path)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def read_text(path: Path | None) -> str:
    if path is None or not path.exists():
        return "报告文件不存在。"
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")
