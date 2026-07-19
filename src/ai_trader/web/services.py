"""Read-only data services for the local dashboard."""

from __future__ import annotations

import json
import inspect
import platform
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from ..db.connection import connect
from ..db.consistency import reconcile_database
from ..db.repositories import count_rows, table_exists
from ..db.strategy_contexts import CONTEXT_FIELDS, get_strategy_context, list_strategy_context_revisions
from ..db.validators import REQUIRED_TABLES, summary_database, validate_database
from ..db.watchlists import sync_positions_tab
from ..file_store import read_jsonl
from ..portfolio import now_iso
from ..strategies import build_builtin_registry
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
    with closing(conn):
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def one(settings: DashboardSettings, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    conn = connect_if_ready(settings)
    if conn is None:
        return None
    with closing(conn):
        row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def safe_rows(settings: DashboardSettings, table: str, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn = connect_if_ready(settings)
    if conn is None:
        return []
    with closing(conn):
        if not table_exists(conn, table):
            return []
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def safe_count(settings: DashboardSettings, table: str) -> int:
    conn = connect_if_ready(settings)
    if conn is None:
        return 0
    with closing(conn):
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
               buy_logic, invalidation_point, stop_loss_price, target_price, position_note, planned_position_pct,
               position_status, updated_at
        FROM positions
        ORDER BY account_id, COALESCE(position_status, 'ACTIVE'), symbol
        """,
    )


def position_detail(settings: DashboardSettings, account_id: str, symbol: str) -> dict[str, Any] | None:
    position = one(
        settings,
        """
        SELECT account_id, symbol, name, asset_type, total_quantity, available_quantity,
               locked_quantity, avg_cost, market_price, market_value, unrealized_pnl,
               unrealized_pnl_pct, position_pct, first_buy_date, last_trade_date,
               buy_logic, invalidation_point, stop_loss_price, target_price, position_note,
               planned_position_pct, position_status, updated_at, payload_json
        FROM positions
        WHERE account_id = ? AND symbol = ?
        """,
        (account_id, symbol),
    )
    if position is None:
        return None
    position["payload"] = parse_json(position.get("payload_json"), {})
    position["recent_decisions"] = decisions(settings, symbol=symbol, limit=12)
    position["latest_decision"] = position["recent_decisions"][0] if position["recent_decisions"] else None
    position["audit_logs"] = safe_rows(
        settings,
        "audit_logs",
        """
        SELECT audit_id, operation, before_value_json, after_value_json, reason, operator, created_at
        FROM audit_logs
        WHERE target_type = 'POSITION' AND target_id = ?
        ORDER BY created_at DESC
        LIMIT 10
        """,
        (f"{account_id}:{symbol}",),
    )
    for audit in position["audit_logs"]:
        audit["before_value"] = parse_json(audit.get("before_value_json"), {})
        audit["after_value"] = parse_json(audit.get("after_value_json"), {})
    return position


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
    legacy_items = safe_rows(
        settings,
        "decision_results",
        f"""
        SELECT decision_id, snapshot_id, symbol, name, task_type, trade_date,
               decision_time, strategy_version, schema_version, final_action,
               confidence, action_reason, human_review_required, artifact_id, created_at,
               payload_json
        FROM decision_results
        {where_sql}
        ORDER BY COALESCE(decision_time, created_at, '') DESC
        LIMIT ?
        """,
        tuple(params),
    )
    for item in legacy_items:
        enrich_decision_item(item)
    strategy_items = _strategy_analysis_sessions(
        settings,
        symbol=symbol,
        action=action,
        task_type=task_type,
        limit=limit,
    )
    items = strategy_items + legacy_items
    items.sort(key=lambda item: item.get("decision_time") or item.get("created_at") or "", reverse=True)
    return items[:limit]


def strategy_context_page_data(settings: DashboardSettings, symbol: str) -> dict[str, Any]:
    normalized = str(symbol or "").strip()
    if len(normalized) != 6 or not normalized.isdigit():
        raise ValueError("股票代码必须是 6 位数字")
    profile: dict[str, Any] = {field: None for field in CONTEXT_FIELDS}
    profile["holding_period"] = "middle"
    with closing(connect(settings.db_path)) as conn:
        saved = get_strategy_context(conn, normalized)
        if saved:
            profile.update(saved)
        revisions = list_strategy_context_revisions(conn, normalized, limit=8)
        quote = conn.execute(
            "SELECT symbol, name, trade_date, price FROM market_quotes WHERE symbol = ?",
            (normalized,),
        ).fetchone()
        position = conn.execute(
            """
            SELECT account_id, name, buy_logic, invalidation_point, stop_loss_price,
                   total_quantity, avg_cost, position_pct
            FROM positions
            WHERE symbol=? AND COALESCE(position_status, 'ACTIVE') != 'CLOSED'
            ORDER BY account_id
            LIMIT 1
            """,
            (normalized,),
        ).fetchone()
    quote_item = dict(quote) if quote else {}
    position_item = dict(position) if position else None
    return {
        "symbol": normalized,
        "name": quote_item.get("name") or (position_item or {}).get("name"),
        "quote": quote_item,
        "position": position_item,
        "profile": profile,
        "revisions": revisions,
        "suggested_core_logic": profile.get("core_logic") or (position_item or {}).get("buy_logic") or "",
        "suggested_technical_invalidation": profile.get("technical_invalidation")
        or (position_item or {}).get("invalidation_point")
        or (position_item or {}).get("stop_loss_price")
        or "",
    }


def _strategy_analysis_sessions(
    settings: DashboardSettings,
    *,
    symbol: str | None,
    action: str | None,
    task_type: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    where: list[str] = []
    params: list[Any] = []
    if symbol:
        where.append("symbol = ?")
        params.append(symbol)
    if action:
        where.append("(buy_conclusion = ? OR holding_conclusion = ?)")
        params.extend([action, action])
    if task_type == "BUY_MULTI_STRATEGY":
        where.append("has_position = 0")
    elif task_type == "POSITION_MULTI_STRATEGY":
        where.append("has_position = 1")
    elif task_type:
        return []
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(limit)
    rows = safe_rows(
        settings,
        "strategy_analysis_sessions",
        f"""
        SELECT analysis_id, symbol, name, trade_date, decision_time, source,
               has_position, buy_snapshot_id, buy_aggregation_id, buy_conclusion,
               holding_snapshot_id, holding_aggregation_id, holding_conclusion,
               effective_strategy_count, blocked_strategy_count, failed_strategy_count,
               status, report_relative_path, created_at
        FROM strategy_analysis_sessions
        {where_sql}
        ORDER BY decision_time DESC
        LIMIT ?
        """,
        tuple(params),
    )
    for item in rows:
        item["decision_id"] = item["analysis_id"]
        item["snapshot_id"] = item.get("holding_snapshot_id") or item.get("buy_snapshot_id")
        item["task_type"] = "POSITION_MULTI_STRATEGY" if item.get("has_position") else "BUY_MULTI_STRATEGY"
        item["final_action"] = item.get("holding_conclusion") or item.get("buy_conclusion")
        item["confidence"] = f"{item.get('effective_strategy_count') or 0} 条有效"
        item["human_review_required"] = 1
        item["schema_version"] = "strategy_analysis_session.v0.2"
        item["strategy_version"] = "strategy_platform.v0.2"
        item["is_strategy_platform"] = True
        item["is_combined"] = bool(item.get("has_position"))
        item["display_task"] = decision_task_label(item["task_type"])
        item["display_action"] = _strategy_session_action(item)
        item["display_reason"] = (
            f"有效 {item.get('effective_strategy_count') or 0}，"
            f"阻断 {item.get('blocked_strategy_count') or 0}，"
            f"异常 {item.get('failed_strategy_count') or 0}；未生成统一总分"
        )
    return rows


def _strategy_session_action(item: dict[str, Any]) -> str:
    buy = strategy_conclusion_label(item.get("buy_conclusion"), TaskTypeLabel.BUY)
    if item.get("has_position"):
        holding = strategy_conclusion_label(item.get("holding_conclusion"), TaskTypeLabel.HOLDING)
        return f"持仓：{holding} / 买入：{buy}"
    return buy


class TaskTypeLabel:
    BUY = "BUY"
    HOLDING = "HOLDING"


def strategy_conclusion_label(value: Any, task: str) -> str:
    if task == TaskTypeLabel.HOLDING:
        return {
            "FAVORABLE": "支持持有",
            "MIXED": "持有与退出冲突",
            "UNFAVORABLE": "支持减仓/退出",
            "INSUFFICIENT": "持仓证据不足",
        }.get(str(value or ""), str(value or ""))
    return {
        "FAVORABLE": "整体支持",
        "MIXED": "结论冲突",
        "UNFAVORABLE": "整体反对",
        "INSUFFICIENT": "证据不足",
    }.get(str(value or ""), str(value or ""))


def enrich_decision_item(item: dict[str, Any]) -> None:
    payload = parse_json(item.get("payload_json"), {})
    item["display_task"] = decision_task_label(item.get("task_type"))
    item["display_action"] = decision_action_label(item.get("final_action"))
    item["display_reason"] = item.get("action_reason")
    item["is_combined"] = item.get("task_type") == "POSITION_COMBINED_REVIEW"
    if item["is_combined"]:
        holding = payload.get("holding_review") or {}
        buy = payload.get("buy_evaluation") or {}
        item["holding_action"] = holding.get("final_action")
        item["buy_action"] = buy.get("final_action")
        item["display_action"] = f"持仓：{decision_action_label(item['holding_action'])} / 买入：{decision_action_label(item['buy_action'])}"
        item["display_reason"] = f"持仓：{decision_reason_label(holding.get('action_reason'))}；买入：{decision_reason_label(buy.get('action_reason'))}"


def decision_task_label(value: Any) -> str:
    return {
        "BUY_EVALUATION": "买入评估",
        "HOLDING_REVIEW": "持仓复查",
        "POSITION_COMBINED_REVIEW": "持仓合并决策",
        "BUY_MULTI_STRATEGY": "多策略买入研究",
        "POSITION_MULTI_STRATEGY": "多策略持仓综合研究",
    }.get(str(value or ""), str(value or ""))


def decision_action_label(value: Any) -> str:
    return {
        "BUY": "买入",
        "WATCH_SMALL": "小仓观察",
        "WAIT": "等待",
        "DO_NOT_BUY": "不买",
        "DATA_BLOCKED": "数据不足",
        "HOLD": "持有",
        "REDUCE_HALF": "减仓一半",
        "REDUCE_TO_WATCH": "减到观察仓",
        "CLEAR": "清仓",
        "NO_SELL_T_PLUS": "T+1 暂不可卖",
        "PRE_EVALUATION": "预评估",
    }.get(str(value or ""), str(value or ""))


def decision_reason_label(value: Any) -> str:
    return {
        "price is below MA20": "价格跌破 MA20",
        "price is below MA60": "价格跌破 MA60",
        "price is below the recent 20-day low": "价格跌破近 20 日低点",
        "no sell trigger is active": "未触发卖出规则",
        "trend confirmation is incomplete": "趋势确认不完整",
        "short-term gain is high; only small watch position is allowed": "短期涨幅较高，只适合小仓观察",
        "holding cost, position, buy logic, or invalidation point is missing": "持仓上下文不完整",
    }.get(str(value or ""), str(value or ""))


def decision_detail(settings: DashboardSettings, decision_id: str) -> dict[str, Any] | None:
    strategy_detail = _strategy_analysis_detail(settings, decision_id)
    if strategy_detail is not None:
        return strategy_detail
    item = one(
        settings,
        """
        SELECT decision_id, snapshot_id, symbol, name, task_type, trade_date,
               decision_time, strategy_version, schema_version, final_action,
               confidence, action_reason, human_review_required, trigger_prices_json,
               payload_json, artifact_id, created_at
        FROM decision_results
        WHERE decision_id = ?
        """,
        (decision_id,),
    )
    if item is None:
        return None
    enrich_decision_item(item)
    payload = parse_json(item.get("payload_json"), {})
    item["payload"] = payload
    item["trigger_prices"] = parse_json(item.get("trigger_prices_json"), {})
    item["rule_results"] = payload.get("rule_results") or []
    item["data_quality"] = payload.get("data_quality_summary") or {}
    item["time_context"] = payload.get("time_context") or {}
    item["holding_review"] = payload.get("holding_review") or None
    item["buy_evaluation"] = payload.get("buy_evaluation") or None
    for section in ["holding_review", "buy_evaluation"]:
        if item.get(section):
            item[section]["display_action"] = decision_action_label(item[section].get("final_action"))
            item[section]["display_reason"] = decision_reason_label(item[section].get("action_reason"))
    report = one(
        settings,
        """
        SELECT report_id, relative_path
        FROM reports
        WHERE source_type='decision_result' AND source_id=?
        ORDER BY COALESCE(created_at, '') DESC
        LIMIT 1
        """,
        (decision_id,),
    )
    item["report"] = report
    return item


def _strategy_analysis_detail(settings: DashboardSettings, analysis_id: str) -> dict[str, Any] | None:
    items = safe_rows(
        settings,
        "strategy_analysis_sessions",
        """
        SELECT analysis_id, symbol, name, trade_date, decision_time, source,
               has_position, buy_conclusion, holding_conclusion,
               effective_strategy_count, blocked_strategy_count, failed_strategy_count,
               status, report_relative_path, payload_json, created_at
        FROM strategy_analysis_sessions
        WHERE analysis_id = ?
        """,
        (analysis_id,),
    )
    if not items:
        return None
    item = items[0]
    payload = parse_json(item.get("payload_json"), {})
    item.update(
        {
            "decision_id": item["analysis_id"],
            "is_strategy_platform": True,
            "is_combined": bool(item.get("has_position")),
            "task_type": "POSITION_MULTI_STRATEGY" if item.get("has_position") else "BUY_MULTI_STRATEGY",
            "display_task": decision_task_label(
                "POSITION_MULTI_STRATEGY" if item.get("has_position") else "BUY_MULTI_STRATEGY"
            ),
            "display_action": _strategy_session_action(item),
            "confidence": f"{item.get('effective_strategy_count') or 0} 条有效策略",
            "human_review_required": True,
            "payload": payload,
            "strategy_sections": [],
        }
    )
    for key, title in (("holding", "持仓策略"), ("buy", "买入 / 加仓策略")):
        section = payload.get(key)
        if not section:
            continue
        snapshot = section.get("snapshot") or {}
        run = section.get("run") or {}
        aggregation = section.get("aggregation") or {}
        task = snapshot.get("task_type") or "BUY"
        evaluations = run.get("evaluations") or []
        for evaluation in evaluations:
            metadata = evaluation.get("metadata") or {}
            evaluation["display_name"] = metadata.get("name") or metadata.get("strategy_id")
            evaluation["display_signal"] = strategy_signal_label(evaluation.get("signal"))
            evaluation["display_score"] = (
                "不评分" if evaluation.get("raw_score") is None else evaluation.get("raw_score")
            )
        item["strategy_sections"].append(
            {
                "title": title,
                "task_type": task,
                "conclusion": aggregation.get("conclusion"),
                "display_conclusion": strategy_conclusion_label(
                    aggregation.get("conclusion"),
                    TaskTypeLabel.HOLDING if task == "HOLDING" else TaskTypeLabel.BUY,
                ),
                "effective_strategy_count": aggregation.get("effective_strategy_count"),
                "support_count": aggregation.get("support_count"),
                "oppose_count": aggregation.get("oppose_count"),
                "neutral_count": aggregation.get("neutral_count"),
                "unknown_count": aggregation.get("unknown_count"),
                "conflicts": aggregation.get("conflicts") or [],
                "family_summary": aggregation.get("family_summary") or [],
                "evaluations": evaluations,
                "data_quality": snapshot.get("data_quality") or {},
                "market_phase": snapshot.get("market_phase"),
                "source_cutoff_time": snapshot.get("source_cutoff_time"),
                "registry_version": run.get("registry_version"),
                "aggregator_version": aggregation.get("aggregator_version"),
            }
        )
    item["report"] = one(
        settings,
        """
        SELECT report_id, relative_path
        FROM reports
        WHERE source_type='strategy_analysis_session' AND source_id=?
        ORDER BY COALESCE(created_at, '') DESC
        LIMIT 1
        """,
        (analysis_id,),
    )
    return item


def strategy_signal_label(value: Any) -> str:
    return {
        "STRONG_SUPPORT": "强支持",
        "SUPPORT": "支持",
        "NEUTRAL": "中性",
        "OPPOSE": "反对",
        "STRONG_OPPOSE": "强反对",
        "HOLD_SUPPORT": "支持持有",
        "REDUCE_SUPPORT": "支持减仓",
        "EXIT_SUPPORT": "支持退出",
        "UNKNOWN": "未知",
    }.get(str(value or ""), str(value or ""))


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
    position_checks = safe_rows(
        settings,
        "position_pre_market_checks",
        """
        SELECT check_id, account_id, symbol, name, trade_date, check_time,
               category, severity, rule_code, message, current_price,
               reference_price, position_pct, available_quantity, locked_quantity,
               review_status, review_action, reviewed_at, review_note
        FROM position_pre_market_checks
        ORDER BY COALESCE(check_time, '') DESC,
                 CASE severity WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 WHEN 'LOW' THEN 3 ELSE 9 END,
                 symbol
        LIMIT ?
        """,
        (limit,),
    )
    for item in position_checks:
        review_note = parse_json(item.get("review_note"), {})
        item["decision_id"] = review_note.get("decision_id") if isinstance(review_note, dict) else None
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
        "position_checks": position_checks,
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


def strategy_catalog() -> dict[str, Any]:
    """Expose the actual registered strategy code and scoring configuration."""
    registry = build_builtin_registry()
    project_root = Path(__file__).resolve().parents[3]
    items: list[dict[str, Any]] = []
    for entry in registry.entries():
        metadata = entry.metadata
        implementation_path = Path(inspect.getfile(entry.strategy.__class__)).resolve()
        strategy_dir = implementation_path.parent
        rule_weights = [
            {
                "rule_id": rule_id,
                "weights": [
                    {"status": status.value, "delta": delta}
                    for status, delta in sorted(weights.items(), key=lambda item: item[0].value)
                ],
            }
            for rule_id, weights in sorted(entry.scoring.rule_weights.items())
        ]
        items.append(
            {
                "strategy_id": metadata.strategy_id,
                "name": metadata.name,
                "family": metadata.strategy_family,
                "version": metadata.strategy_version,
                "parameter_version": metadata.parameter_version,
                "task_type": metadata.task_type.value,
                "task_label": "买入 / 加仓" if metadata.task_type.value == "BUY" else "持仓退出",
                "implementation_type": metadata.implementation_type.value,
                "maturity": metadata.maturity.value,
                "calibration_status": metadata.calibration_status.value,
                "aggregation_role": metadata.aggregation_role.value,
                "enabled": metadata.enabled,
                "asset_types": list(metadata.supported_asset_types),
                "market_phases": [phase.value for phase in metadata.supported_market_phases],
                "required_features": list(metadata.required_features),
                "optional_features": list(metadata.optional_features),
                "implementation_class": (
                    f"{entry.strategy.__class__.__module__}.{entry.strategy.__class__.__qualname__}"
                ),
                "implementation_path": _project_relative_path(implementation_path, project_root),
                "metadata_path": _project_relative_path(strategy_dir / "metadata.json", project_root),
                "scoring_path": _project_relative_path(strategy_dir / "scoring.json", project_root),
                "base_score": entry.scoring.base_score,
                "score_range": f"{entry.scoring.min_score:g} - {entry.scoring.max_score:g}",
                "config_hash": entry.scoring.config_hash,
                "rule_weights": rule_weights,
                "thresholds": [
                    {"min_score": threshold.min_score, "signal": threshold.signal.value}
                    for threshold in entry.scoring.thresholds
                ],
            }
        )
    return {
        "registry_version": registry.version,
        "strategies": items,
        "strategy_count": len(items),
        "enabled_count": sum(1 for item in items if item["enabled"]),
        "rule_based_count": sum(1 for item in items if item["implementation_type"] == "RULE_BASED"),
        "ai_based_count": sum(1 for item in items if item["implementation_type"] == "AI_BASED"),
    }


def _project_relative_path(path: Path, project_root: Path) -> str:
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return path.as_posix()


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


def watchlists(settings: DashboardSettings) -> dict[str, Any]:
    tab_id = "all"
    q = ""
    page = 1
    page_size = 20
    return watchlists_page_data(settings, tab_id=tab_id, q=q, page=page, page_size=page_size)


def watchlists_page_data(
    settings: DashboardSettings,
    *,
    tab_id: str,
    q: str,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    conn = connect_if_ready(settings)
    if conn is None:
        return empty_watchlist_page(tab_id, q, page, page_size)
    with conn:
        if not table_exists(conn, "watchlist_tabs") or not table_exists(conn, "watchlist_items"):
            return empty_watchlist_page(tab_id, q, page, page_size)
        sync_positions_tab(conn)
        conn.commit()
        tabs = [
            dict(row)
            for row in conn.execute(
                """
                SELECT t.tab_id, t.name, t.tab_type, t.sort_order, t.is_default,
                       COUNT(i.item_id) AS count
                FROM watchlist_tabs t
                LEFT JOIN watchlist_items i ON i.tab_id = t.tab_id
                WHERE t.is_active = 1
                GROUP BY t.tab_id, t.name, t.tab_type, t.sort_order, t.is_default
                ORDER BY t.sort_order, t.created_at
                """
            ).fetchall()
        ]
        if not tabs:
            return empty_watchlist_page(tab_id, q, page, page_size)
        tab_ids = {tab["tab_id"] for tab in tabs}
        active_tab_id = tab_id if tab_id in tab_ids else tabs[0]["tab_id"]
        active_tab = next(tab for tab in tabs if tab["tab_id"] == active_tab_id)
        page_size = max(5, min(int(page_size or 20), 100))
        page = max(1, int(page or 1))
        search = str(q or "").strip()
        where = ["i.tab_id = ?"]
        params: list[Any] = [active_tab_id]
        if search:
            where.append("(i.symbol LIKE ? OR COALESCE(m.name, i.name, '') LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like])
        where_sql = " AND ".join(where)
        total = conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM watchlist_items i
            LEFT JOIN market_quotes m ON m.symbol = i.symbol
            WHERE {where_sql}
            """,
            tuple(params),
        ).fetchone()["count"]
        page_count = max(1, (int(total) + page_size - 1) // page_size)
        page = min(page, page_count)
        offset = (page - 1) * page_size
        item_params = params + [page_size, offset]
        stocks = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT
                    i.item_id, i.tab_id, i.symbol AS code,
                    COALESCE(m.name, i.name) AS name,
                    m.price, m.pct_change, m.pe_ttm, m.pb, m.ma20, m.ma60,
                    m.change_20d_pct, m.trade_date, m.updated_at,
                    CASE WHEN m.symbol IS NULL THEN 'MISSING' ELSE 'OK' END AS status
                FROM watchlist_items i
                LEFT JOIN market_quotes m ON m.symbol = i.symbol
                WHERE {where_sql}
                ORDER BY i.symbol
                LIMIT ? OFFSET ?
                """,
                tuple(item_params),
            ).fetchall()
        ]
        for stock in stocks:
            stock["pct_class"] = market_change_class(stock.get("pct_change"))
            stock["change_20d_class"] = market_change_class(stock.get("change_20d_pct"))
    return {
        "tabs": tabs,
        "active_tab": active_tab,
        "stocks": stocks,
        "filters": {"tab": active_tab_id, "q": search, "page": page, "page_size": page_size},
        "pagination": {
            "total": int(total),
            "page": page,
            "page_size": page_size,
            "page_count": page_count,
            "prev_page": page - 1 if page > 1 else None,
            "next_page": page + 1 if page < page_count else None,
        },
    }


def market_change_class(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number > 0:
        return "market-up"
    if number < 0:
        return "market-down"
    return "market-flat"


def empty_watchlist_page(tab_id: str, q: str, page: int, page_size: int) -> dict[str, Any]:
    return {
        "tabs": [],
        "active_tab": {"tab_id": tab_id, "name": "自选股", "count": 0},
        "stocks": [],
        "filters": {"tab": tab_id, "q": q, "page": page, "page_size": page_size},
        "pagination": {"total": 0, "page": 1, "page_size": page_size, "page_count": 1, "prev_page": None, "next_page": None},
    }


def stock_detail(settings: DashboardSettings, code: str) -> dict[str, Any] | None:
    item = one(
        settings,
        """
        SELECT symbol AS code, name, exchange, asset_type, trade_date, quote_time,
               price, pct_change, pe_ttm, pb, market_cap_yuan, ma20, ma60,
               change_20d_pct, source, source_path, updated_at, payload_json
        FROM market_quotes
        WHERE symbol = ?
        """,
        (code,),
    )
    if item is None:
        return None
    payload = parse_json(item.get("payload_json"), {})
    quote = payload.get("quote") or {}
    meta = payload.get("meta") or {}
    return {
        "code": item.get("code"),
        "path": item.get("source_path"),
        "name": item.get("name"),
        "trade_date": item.get("trade_date"),
        "generated_at": item.get("updated_at") or meta.get("generated_at"),
        "quote": quote or item,
        "technical": payload.get("technical") or {},
        "valuation": payload.get("valuation") or {},
        "financial": payload.get("financial") or {},
        "data_gaps": payload.get("data_gaps_for_engine") or [],
        "raw_json": json.dumps(payload or item, ensure_ascii=False, indent=2),
    }


def watchlist_screen_runs(settings: DashboardSettings, limit: int = 50) -> list[dict[str, Any]]:
    items = safe_rows(
        settings,
        "watchlist_screen_runs",
        """
        SELECT run_id, tab_id, tab_name, screen_type, trade_date, total_count,
               candidate_count, watch_count, risk_count, data_gap_count, created_at
        FROM watchlist_screen_runs
        ORDER BY COALESCE(created_at, '') DESC
        LIMIT ?
        """,
        (limit,),
    )
    for item in items:
        item["summary"] = (
            f"候选 {item.get('candidate_count') or 0} / "
            f"观察 {item.get('watch_count') or 0} / "
            f"风险 {item.get('risk_count') or 0} / "
            f"缺数据 {item.get('data_gap_count') or 0}"
        )
    return items


def watchlist_screen_detail(settings: DashboardSettings, run_id: str) -> dict[str, Any] | None:
    run = one(
        settings,
        """
        SELECT run_id, tab_id, tab_name, screen_type, trade_date, total_count,
               candidate_count, watch_count, risk_count, data_gap_count, created_at, params_json
        FROM watchlist_screen_runs
        WHERE run_id = ?
        """,
        (run_id,),
    )
    if run is None:
        return None
    results = safe_rows(
        settings,
        "watchlist_screen_results",
        """
        SELECT result_id, run_id, symbol, name, category, score, price, pct_change,
               pe_ttm, pb, ma20, ma60, change_20d_pct, trade_date, is_position,
               matched_rules_json, warnings_json, summary, created_at,
               review_status, review_action, reviewed_at, review_note
        FROM watchlist_screen_results
        WHERE run_id = ?
        ORDER BY
            CASE category
                WHEN 'CANDIDATE' THEN 1
                WHEN 'WATCH' THEN 2
                WHEN 'NEUTRAL' THEN 3
                WHEN 'RISK' THEN 4
                WHEN 'DATA_GAP' THEN 5
                ELSE 9
            END,
            score DESC,
            symbol
        """,
        (run_id,),
    )
    for item in results:
        item["review_status"] = item.get("review_status") or "UNREVIEWED"
        item["matched_rules"] = parse_json(item.get("matched_rules_json"), [])
        item["warnings"] = parse_json(item.get("warnings_json"), [])
        review_note = parse_json(item.get("review_note"), {})
        item["decision_id"] = review_note.get("decision_id") if isinstance(review_note, dict) else None
        item["pct_class"] = market_change_class(item.get("pct_change"))
        item["change_20d_class"] = market_change_class(item.get("change_20d_pct"))
    return {"run": run, "results": results}


def post_market_diagnosis_page(settings: DashboardSettings, limit: int = 100) -> dict[str, Any]:
    conn = connect_if_ready(settings)
    if conn is None:
        return {"tabs": [], "runs": [], "items": []}
    with conn:
        tabs = (
            [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT tab_id, name, tab_type
                    FROM watchlist_tabs
                    WHERE is_active = 1
                    ORDER BY sort_order, created_at
                    """
                ).fetchall()
            ]
            if table_exists(conn, "watchlist_tabs")
            else []
        )
    runs = safe_rows(
        settings,
        "post_market_diagnosis_runs",
        """
        SELECT run_id, trade_date, next_trade_date, total_count, success_count,
               failed_count, position_risk_count, buy_candidate_count, watch_count,
               data_gap_count, created_at
        FROM post_market_diagnosis_runs
        ORDER BY COALESCE(created_at, '') DESC
        LIMIT 30
        """,
    )
    latest_data_prep = safe_rows(
        settings,
        "post_market_data_prep_runs",
        """
        SELECT run_id, trade_date, success_count, failed_count, status, finished_at
        FROM post_market_data_prep_runs
        ORDER BY COALESCE(started_at, '') DESC
        LIMIT 1
        """,
    )
    latest_run_id = runs[0]["run_id"] if runs else None
    items = (
        safe_rows(
            settings,
            "next_day_watch_items",
            """
            SELECT item_id, run_id, symbol, name, source_type, category, priority,
                   reason, current_price, reference_price, trade_date, next_trade_date,
                   review_status, review_action, reviewed_at, decision_id, created_at
            FROM next_day_watch_items
            WHERE run_id = ?
            ORDER BY CASE priority WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 WHEN 'LOW' THEN 3 ELSE 9 END,
                     CASE category WHEN 'POSITION_RISK' THEN 1 WHEN 'BUY_CANDIDATE' THEN 2 WHEN 'WATCH_TOMORROW' THEN 3 WHEN 'DATA_GAP' THEN 4 ELSE 9 END,
                     symbol
            LIMIT ?
            """,
            (latest_run_id, limit),
        )
        if latest_run_id
        else []
    )
    for item in items:
        item["price_class"] = market_change_class(None)
    return {
        "tabs": tabs,
        "runs": runs,
        "items": items,
        "latest_run_id": latest_run_id,
        "latest_data_prep": latest_data_prep[0] if latest_data_prep else None,
    }


def post_market_data_prep_page(settings: DashboardSettings, limit: int = 80) -> dict[str, Any]:
    conn = connect_if_ready(settings)
    if conn is None:
        return {"tabs": [], "runs": [], "items": [], "latest_run_id": None}
    with conn:
        tabs = (
            [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT tab_id, name, tab_type
                    FROM watchlist_tabs
                    WHERE is_active = 1
                    ORDER BY sort_order, created_at
                    """
                ).fetchall()
            ]
            if table_exists(conn, "watchlist_tabs")
            else []
        )
    runs = safe_rows(
        settings,
        "post_market_data_prep_runs",
        """
        SELECT run_id, trade_date, total_count, success_count, failed_count,
               position_sync_count, account_sync_count, started_at, finished_at,
               status, error_message
        FROM post_market_data_prep_runs
        ORDER BY COALESCE(started_at, '') DESC
        LIMIT 30
        """,
    )
    latest_run_id = runs[0]["run_id"] if runs else None
    items = (
        safe_rows(
            settings,
            "post_market_data_prep_items",
            """
            SELECT item_id, run_id, symbol, name, source_type, status,
                   trade_date, error_message, created_at
            FROM post_market_data_prep_items
            WHERE run_id=?
            ORDER BY CASE status WHEN 'FAILED' THEN 1 WHEN 'OK' THEN 2 ELSE 9 END,
                     symbol
            LIMIT ?
            """,
            (latest_run_id, limit),
        )
        if latest_run_id
        else []
    )
    full_market_batches = safe_rows(
        settings,
        "market_data_batches",
        """
        SELECT batch_id, trade_date, started_at, finished_at, status,
               total_count, success_count, failed_count, error_message
        FROM market_data_batches
        WHERE batch_type='POST_MARKET_FULL'
        ORDER BY COALESCE(started_at, '') DESC
        LIMIT 5
        """,
    )
    latest_full_market = full_market_batches[0] if full_market_batches else None
    full_market_failed_items = (
        safe_rows(
            settings,
            "market_data_batch_items",
            """
            SELECT symbol, name, status, trade_date, error_message, finished_at
            FROM market_data_batch_items
            WHERE batch_id=? AND status='FAILED'
            ORDER BY symbol
            LIMIT 30
            """,
            (latest_full_market["batch_id"],),
        )
        if latest_full_market
        else []
    )
    if latest_full_market:
        total = int(latest_full_market.get("total_count") or 0)
        done = int(latest_full_market.get("success_count") or 0) + int(latest_full_market.get("failed_count") or 0)
        latest_full_market["done_count"] = done
        latest_full_market["progress_pct"] = round((done / total) * 100, 2) if total else 0
        latest_full_market["running"] = latest_full_market.get("status") == "RUNNING"
    close_runs = safe_rows(
        settings,
        "post_market_close_runs",
        """
        SELECT run_id, started_at, finished_at, status, current_step,
               full_market_status, prepare_status, diagnosis_status, watchlist_status,
               market_batch_id, prepare_run_id, diagnosis_run_id, total_count,
               success_count, failed_count, next_watch_count, error_message
        FROM post_market_close_runs
        ORDER BY COALESCE(started_at, '') DESC
        LIMIT 5
        """,
    )
    latest_close_run = close_runs[0] if close_runs else None
    if latest_close_run:
        total = int(latest_close_run.get("total_count") or 0)
        done = int(latest_close_run.get("success_count") or 0) + int(latest_close_run.get("failed_count") or 0)
        latest_close_run["done_count"] = done
        latest_close_run["progress_pct"] = round(done / total * 100, 2) if total else 0
        latest_close_run["running"] = latest_close_run.get("status") == "RUNNING"
    return {
        "tabs": tabs,
        "runs": runs,
        "items": items,
        "latest_run_id": latest_run_id,
        "close_runs": close_runs,
        "latest_close_run": latest_close_run,
        "full_market_batches": full_market_batches,
        "latest_full_market": latest_full_market,
        "full_market_failed_items": full_market_failed_items,
    }


def historical_data_page(settings: DashboardSettings, limit: int = 80) -> dict[str, Any]:
    batches = safe_rows(
        settings,
        "market_data_batches",
        """
        SELECT batch_id, batch_type, trade_date, session_type, scope_type,
               started_at, finished_at, status, total_count, success_count,
               failed_count, source, error_message
        FROM market_data_batches
        ORDER BY COALESCE(started_at, '') DESC
        LIMIT 30
        """,
    )
    latest_trade_date_row = safe_rows(
        settings,
        "daily_market_snapshots",
        "SELECT MAX(trade_date) AS trade_date FROM daily_market_snapshots",
    )
    latest_trade_date = latest_trade_date_row[0].get("trade_date") if latest_trade_date_row else None
    snapshots = (
        safe_rows(
            settings,
            "daily_market_snapshots",
            """
            SELECT symbol, name, trade_date, close, pct_change, pe_ttm, pb,
                   ma20, ma60, data_origin, quality_status, observed_at,
                   batch_id
            FROM daily_market_snapshots
            WHERE trade_date=?
            ORDER BY symbol
            LIMIT ?
            """,
            (latest_trade_date, limit),
        )
        if latest_trade_date
        else []
    )
    counts = safe_rows(
        settings,
        "daily_market_snapshots",
        """
        SELECT trade_date, COUNT(*) AS count,
               SUM(CASE WHEN data_origin='LIVE_CAPTURE' THEN 1 ELSE 0 END) AS live_count,
               SUM(CASE WHEN data_origin='BACKFILL' THEN 1 ELSE 0 END) AS backfill_count
        FROM daily_market_snapshots
        GROUP BY trade_date
        ORDER BY trade_date DESC
        LIMIT 20
        """,
    )
    return {
        "batches": batches,
        "snapshots": snapshots,
        "counts": counts,
        "latest_trade_date": latest_trade_date,
    }


def read_code_list(path: Path) -> list[str]:
    if not path.exists():
        return []
    codes: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        code = line.strip()
        if code and code not in codes:
            codes.append(code)
    return codes


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
