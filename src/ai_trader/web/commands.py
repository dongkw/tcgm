"""Safe dashboard-triggered commands."""

from __future__ import annotations

import csv
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..analysis_snapshot import build_analysis_snapshot
from ..ai_interface.providers import provider_from_name
from ..decision_utils import ensure_dir
from ..db.consistency import reconcile_database
from ..db.connection import connect
from ..db.importers import import_json_ledgers
from ..db.market_history import (
    create_market_data_batch,
    fail_market_data_batch,
    finish_market_data_batch,
    upsert_daily_market_snapshot,
)
from ..db.repositories import json_text
from ..db.strategy_contexts import CONTEXT_FIELDS, get_strategy_context, save_strategy_context
from ..db.writers import relative_path, write_report
from ..db.validators import backup_database, validate_database
from ..db.watchlists import (
    bootstrap_watchlist_database,
    create_watchlist_tab,
    delete_watchlist_tab,
    ensure_default_tabs,
    remove_watchlist_item,
    sync_positions_tab,
    sync_position_valuations,
    upsert_market_quote,
    upsert_watchlist_item,
)
from ..stock_decision_data import build_payload
from ..strategies import build_builtin_registry
from ..strategy_platform.contracts import TaskType
from ..strategy_platform.pipeline import PipelineResult, StrategyPipeline
from ..strategy_platform.report import write_analysis_session_report
from ..strategy_platform.repositories import save_analysis_session, save_pipeline_result
from ..timekeeper import _tzinfo, build_time_context
from ..watchlist_screening import create_screen_run
from ..watchlist_screening import evaluate_quote as evaluate_watchlist_quote
from ..portfolio import compact_now, now_iso
from .settings import DashboardSettings


def import_json(settings: DashboardSettings) -> dict[str, Any]:
    return import_json_ledgers(settings.output_root, str(settings.db_path))


def validate(settings: DashboardSettings) -> dict[str, Any]:
    return validate_database(settings.output_root, str(settings.db_path))


def reconcile(settings: DashboardSettings) -> dict[str, Any]:
    return reconcile_database(settings.output_root, str(settings.db_path))


def backup(settings: DashboardSettings) -> dict[str, Any]:
    return backup_database(settings.output_root, str(settings.db_path))


def generate_position_pre_market_check(settings: DashboardSettings) -> dict[str, Any]:
    check_time = now_iso()
    time_context = build_time_context({}, "PRE_MARKET")
    trade_date = time_context.get("trade_date") or time_context.get("calendar_date")
    rows: list[dict[str, Any]] = []
    with connect(settings.db_path) as conn:
        positions = conn.execute(
            """
            SELECT p.account_id, p.symbol, p.name, p.total_quantity, p.available_quantity,
                   p.locked_quantity, p.avg_cost, p.market_price, p.unrealized_pnl_pct,
                   p.position_pct, p.buy_logic, p.invalidation_point, p.stop_loss_price,
                   p.target_price, p.planned_position_pct, p.position_status,
                   a.max_single_position_pct
            FROM positions p
            LEFT JOIN accounts a ON a.account_id = p.account_id
            WHERE COALESCE(p.position_status, 'ACTIVE') != 'CLOSED'
              AND COALESCE(p.total_quantity, 0) > 0
            ORDER BY p.account_id, p.symbol
            """
        ).fetchall()
        for position in positions:
            rows.extend(_position_pre_market_rules(dict(position), trade_date, check_time))
        conn.execute("DELETE FROM position_pre_market_checks WHERE trade_date = ?", (trade_date,))
        for row in rows:
            conn.execute(
                """
                INSERT INTO position_pre_market_checks (
                    check_id, account_id, symbol, name, trade_date, check_time,
                    category, severity, rule_code, message, current_price,
                    reference_price, position_pct, available_quantity,
                    locked_quantity, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["check_id"],
                    row["account_id"],
                    row["symbol"],
                    row.get("name"),
                    row["trade_date"],
                    row["check_time"],
                    row["category"],
                    row["severity"],
                    row["rule_code"],
                    row["message"],
                    row.get("current_price"),
                    row.get("reference_price"),
                    row.get("position_pct"),
                    row.get("available_quantity"),
                    row.get("locked_quantity"),
                    json_text(row),
                ),
            )
        conn.commit()
    return {
        "trade_date": trade_date,
        "check_time": check_time,
        "position_count": len(positions),
        "issue_count": len(rows),
        "high_count": len([row for row in rows if row["severity"] == "HIGH"]),
        "medium_count": len([row for row in rows if row["severity"] == "MEDIUM"]),
    }


def _as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _position_pre_market_rules(position: dict[str, Any], trade_date: str, check_time: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(rule_code: str, severity: str, category: str, message: str, reference_price: float | None = None) -> None:
        rows.append(
            {
                "check_id": f"pmpos_{trade_date}_{position['account_id']}_{position['symbol']}_{rule_code}",
                "account_id": position["account_id"],
                "symbol": position["symbol"],
                "name": position.get("name"),
                "trade_date": trade_date,
                "check_time": check_time,
                "category": category,
                "severity": severity,
                "rule_code": rule_code,
                "message": message,
                "current_price": _as_float(position.get("market_price")),
                "reference_price": reference_price,
                "position_pct": _as_float(position.get("position_pct")),
                "available_quantity": _as_int(position.get("available_quantity")),
                "locked_quantity": _as_int(position.get("locked_quantity")),
            }
        )

    price = _as_float(position.get("market_price"))
    stop_loss = _as_float(position.get("stop_loss_price")) or _as_float(position.get("invalidation_point"))
    target_price = _as_float(position.get("target_price"))
    pnl_pct = _as_float(position.get("unrealized_pnl_pct"))
    position_pct = _as_float(position.get("position_pct"))
    max_single_pct = _as_float(position.get("max_single_position_pct"))
    planned_pct = _as_float(position.get("planned_position_pct"))
    locked_quantity = _as_int(position.get("locked_quantity"))

    if not str(position.get("buy_logic") or "").strip():
        add("MISSING_BUY_LOGIC", "MEDIUM", "DATA_GAP", "缺少原买入逻辑，持仓卖出判断只能降级")
    if stop_loss is None:
        add("MISSING_EXIT_RULE", "HIGH", "DATA_GAP", "缺少证伪点或止损价，盘中风险无法自动触发")
    if locked_quantity > 0:
        add("T_PLUS_LOCKED", "MEDIUM", "TRADE_LIMIT", f"T+1 锁定 {locked_quantity} 股，今日不能卖出这部分")
    if price is not None and stop_loss is not None:
        if price <= stop_loss:
            add("STOP_LOSS_BROKEN", "HIGH", "SELL_RISK", "当前价已跌破止损/证伪价，需要人工确认是否减仓或清仓", stop_loss)
        elif price <= stop_loss * 1.03:
            add("NEAR_STOP_LOSS", "HIGH", "SELL_RISK", "当前价距离止损/证伪价不足 3%，盘中重点盯盘", stop_loss)
    if pnl_pct is not None and pnl_pct <= -8:
        add("LOSS_WARNING", "MEDIUM", "SELL_RISK", "浮亏超过 8%，需要复核原买入逻辑是否仍成立")
    limit_pct = planned_pct or max_single_pct
    if position_pct is not None and limit_pct is not None and position_pct > limit_pct:
        add("POSITION_TOO_HIGH", "MEDIUM", "POSITION_RISK", "当前仓位超过计划仓位或单票上限，需要控制新增买入", limit_pct)
    if price is not None and target_price is not None and target_price > 0:
        if price >= target_price:
            add("TARGET_REACHED", "MEDIUM", "TAKE_PROFIT", "当前价已达到目标价，需要复核止盈或继续持有条件", target_price)
        elif price >= target_price * 0.97:
            add("NEAR_TARGET_PRICE", "LOW", "TAKE_PROFIT", "当前价距离目标价不足 3%，盘中可观察止盈条件", target_price)

    if not rows:
        add("POSITION_CHECK_OK", "LOW", "OK", "未发现明显盘前持仓风险")
    return rows


def update_position_check_review(
    settings: DashboardSettings,
    check_id: str,
    status: str,
    note: str | None = None,
) -> dict[str, Any]:
    allowed = {"UNREVIEWED", "VIEWED", "DEFER", "NEED_DECISION", "DECISION_CREATED", "DONE", "IGNORE"}
    normalized = status.strip().upper()
    if normalized not in allowed:
        raise ValueError(f"invalid position check review status: {status}")
    with connect(settings.db_path) as conn:
        row = conn.execute(
            "SELECT check_id, trade_date FROM position_pre_market_checks WHERE check_id=?",
            (check_id,),
        ).fetchone()
        if row is None:
            raise ValueError("position check not found")
        conn.execute(
            """
            UPDATE position_pre_market_checks
            SET review_status = ?,
                review_action = ?,
                reviewed_at = ?,
                review_note = ?
            WHERE check_id = ?
            """,
            (normalized, normalized, now_iso(), (note or "").strip() or None, check_id),
        )
        conn.commit()
    return {"check_id": check_id, "trade_date": row["trade_date"], "review_status": normalized}


def refresh_selected_watchlist(settings: DashboardSettings, codes: list[str]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    refreshed_codes: list[str] = []
    with connect(settings.db_path) as conn:
        for code in codes:
            try:
                payload = build_payload(code, "middle")
                upsert_market_quote(conn, code, payload, source_path="web_refresh")
                refreshed_codes.append(code)
                quote = payload.get("quote") or {}
                rows.append(
                    {
                        "code": code,
                        "status": "OK",
                        "name": quote.get("name"),
                        "trade_date": quote.get("trade_date"),
                        "error": None,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - surface per-symbol refresh failures.
                rows.append({"code": code, "status": "FAILED", "name": None, "trade_date": None, "error": str(exc)})
        valuation_sync = sync_position_valuations(conn, refreshed_codes)
        conn.commit()
    ok_rows = [row for row in rows if row["status"] == "OK"]
    failed_rows = [row for row in rows if row["status"] != "OK"]
    return {
        "source": "web_selected_codes",
        "total": len(codes),
        "processed": len(rows),
        "ok_count": len(ok_rows),
        "failed_count": len(failed_rows),
        "rows": rows,
        "failed_codes": [row["code"] for row in failed_rows],
        "valuation_sync": valuation_sync,
    }


def refresh_position_market_data(settings: DashboardSettings) -> dict[str, Any]:
    with connect(settings.db_path) as conn:
        codes = [
            row["symbol"]
            for row in conn.execute(
                """
                SELECT DISTINCT symbol
                FROM positions
                WHERE COALESCE(position_status, 'ACTIVE') != 'CLOSED'
                  AND COALESCE(total_quantity, 0) > 0
                ORDER BY symbol
                """
            ).fetchall()
        ]
    if not codes:
        return {"total": 0, "ok_count": 0, "failed_count": 0, "rows": [], "valuation_sync": {"positions": 0, "accounts": 0}}
    return refresh_selected_watchlist(settings, codes)


def _collect_scope_codes(
    conn,
    *,
    tab_ids: list[str],
    include_positions: bool,
) -> tuple[list[str], dict[str, str]]:
    ensure_default_tabs(conn)
    selected_tabs = [str(tab_id).strip() for tab_id in tab_ids if str(tab_id).strip()]
    codes: list[str] = []
    source_by_symbol: dict[str, str] = {}
    if include_positions:
        for row in conn.execute(
            """
            SELECT symbol
            FROM positions
            WHERE COALESCE(position_status, 'ACTIVE') != 'CLOSED'
              AND COALESCE(total_quantity, 0) > 0
            ORDER BY symbol
            """
        ).fetchall():
            symbol = row["symbol"]
            if symbol not in codes:
                codes.append(symbol)
            source_by_symbol[symbol] = "POSITION"
    if selected_tabs:
        placeholders = ",".join("?" for _ in selected_tabs)
        for row in conn.execute(
            f"""
            SELECT tab_id, symbol
            FROM watchlist_items
            WHERE tab_id IN ({placeholders})
            ORDER BY tab_id, symbol
            """,
            tuple(selected_tabs),
        ).fetchall():
            symbol = row["symbol"]
            if symbol not in codes:
                codes.append(symbol)
            source_by_symbol.setdefault(symbol, "WATCHLIST")
    return codes, source_by_symbol


def run_post_market_data_prep(
    settings: DashboardSettings,
    *,
    tab_ids: list[str],
    include_positions: bool = True,
) -> dict[str, Any]:
    selected_tabs = [str(tab_id).strip() for tab_id in tab_ids if str(tab_id).strip()]
    started_at = now_iso()
    run_id = f"postdata_{compact_now()}"
    with connect(settings.db_path) as conn:
        codes, source_by_symbol = _collect_scope_codes(conn, tab_ids=selected_tabs, include_positions=include_positions)
        create_market_data_batch(
            conn,
            batch_id=run_id,
            batch_type="POST_MARKET_SELECTED",
            session_type="POST_MARKET",
            scope_type="POSITIONS_WATCHLIST",
            scope={"tab_ids": selected_tabs, "include_positions": include_positions, "symbols": codes},
            total_count=len(codes),
            source="post_market_data_prep",
            params={"period": "middle"},
            started_at=started_at,
        )
        conn.execute(
            """
            INSERT INTO post_market_data_prep_runs (
                run_id, source_tabs_json, include_positions, total_count,
                started_at, status, params_json
            )
            VALUES (?, ?, ?, ?, ?, 'RUNNING', ?)
            """,
            (
                run_id,
                json.dumps(selected_tabs, ensure_ascii=False),
                1 if include_positions else 0,
                len(codes),
                started_at,
                json.dumps({"tab_ids": selected_tabs, "include_positions": include_positions}, ensure_ascii=False),
            ),
        )
        conn.commit()

    rows: list[dict[str, Any]] = []
    refreshed_codes: list[str] = []
    run_error: str | None = None
    try:
        with connect(settings.db_path) as conn:
            for index, code in enumerate(codes, start=1):
                item_id = f"{run_id}_item_{index:04d}_{code}"
                try:
                    payload = build_payload(code, "middle")
                    upsert_market_quote(conn, code, payload, source_path="post_market_data_prep")
                    upsert_daily_market_snapshot(
                        conn,
                        code,
                        payload,
                        batch_id=run_id,
                        data_origin="LIVE_CAPTURE",
                        source_version="stock_decision_data.middle",
                    )
                    quote = payload.get("quote") or {}
                    refreshed_codes.append(code)
                    row = {
                        "code": code,
                        "status": "OK",
                        "name": quote.get("name"),
                        "trade_date": quote.get("trade_date"),
                        "error": None,
                    }
                except Exception as exc:  # noqa: BLE001 - keep per-symbol failure visible.
                    row = {
                        "code": code,
                        "status": "FAILED",
                        "name": None,
                        "trade_date": None,
                        "error": str(exc),
                    }
                rows.append(row)
                conn.execute(
                    """
                    INSERT INTO post_market_data_prep_items (
                        item_id, run_id, symbol, name, source_type, status,
                        trade_date, error_message, created_at, payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        run_id,
                        code,
                        row.get("name"),
                        source_by_symbol.get(code, "WATCHLIST"),
                        row["status"],
                        row.get("trade_date"),
                        row.get("error"),
                        now_iso(),
                        json_text(row),
                    ),
                )
            valuation_sync = sync_position_valuations(conn, refreshed_codes)
            ok_rows = [row for row in rows if row["status"] == "OK"]
            failed_rows = [row for row in rows if row["status"] != "OK"]
            trade_date = max((str(row.get("trade_date") or "") for row in ok_rows if row.get("trade_date")), default=None)
            status = "OK" if not failed_rows else "PARTIAL" if ok_rows else "FAILED"
            finish_market_data_batch(
                conn,
                batch_id=run_id,
                trade_date=trade_date,
                status=status,
                success_count=len(ok_rows),
                failed_count=len(failed_rows),
                payload={"rows": rows, "valuation_sync": valuation_sync},
            )
            conn.execute(
                """
                UPDATE post_market_data_prep_runs
                SET trade_date=?, success_count=?, failed_count=?,
                    position_sync_count=?, account_sync_count=?,
                    finished_at=?, status=?, error_message=?, payload_json=?
                WHERE run_id=?
                """,
                (
                    trade_date,
                    len(ok_rows),
                    len(failed_rows),
                    (valuation_sync or {}).get("positions") or 0,
                    (valuation_sync or {}).get("accounts") or 0,
                    now_iso(),
                    status,
                    None,
                    json_text({"rows": rows, "valuation_sync": valuation_sync}),
                    run_id,
                ),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - mark batch failed.
        run_error = str(exc)
        with connect(settings.db_path) as conn:
            fail_market_data_batch(conn, batch_id=run_id, error_message=run_error)
            conn.execute(
                """
                UPDATE post_market_data_prep_runs
                SET finished_at=?, status='FAILED', error_message=?
                WHERE run_id=?
                """,
                (now_iso(), run_error, run_id),
            )
            conn.commit()
        raise

    ok_rows = [row for row in rows if row["status"] == "OK"]
    failed_rows = [row for row in rows if row["status"] != "OK"]
    return {
        "run_id": run_id,
        "total": len(codes),
        "success_count": len(ok_rows),
        "failed_count": len(failed_rows),
        "failed_codes": [row["code"] for row in failed_rows],
        "error": run_error,
    }


def run_post_market_snapshot_prepare(settings: DashboardSettings) -> dict[str, Any]:
    started_at = now_iso()
    run_id = f"postready_{compact_now()}"
    with connect(settings.db_path) as conn:
        latest = conn.execute("SELECT MAX(trade_date) AS trade_date FROM daily_market_snapshots").fetchone()
        trade_date = latest["trade_date"] if latest else None
        if not trade_date:
            raise ValueError("没有可用的日终历史快照，请先执行全市场采集或小范围补采")
        positions = [
            dict(row)
            for row in conn.execute(
                """
                SELECT symbol, COALESCE(name, symbol) AS name
                FROM positions
                WHERE COALESCE(position_status, 'ACTIVE') != 'CLOSED'
                  AND COALESCE(total_quantity, 0) > 0
                ORDER BY symbol
                """
            ).fetchall()
        ]
        conn.execute(
            """
            INSERT INTO post_market_data_prep_runs (
                run_id, trade_date, source_tabs_json, include_positions,
                total_count, started_at, status, params_json
            )
            VALUES (?, ?, '[]', 1, ?, ?, 'RUNNING', ?)
            """,
            (
                run_id,
                trade_date,
                len(positions),
                started_at,
                json.dumps({"mode": "SNAPSHOT_PREPARE", "source": "daily_market_snapshots"}, ensure_ascii=False),
            ),
        )
        ok_symbols: list[str] = []
        missing_symbols: list[str] = []
        for index, position in enumerate(positions, start=1):
            symbol = position["symbol"]
            snapshot = conn.execute(
                """
                SELECT *
                FROM daily_market_snapshots
                WHERE symbol=? AND trade_date=?
                """,
                (symbol, trade_date),
            ).fetchone()
            status = "OK" if snapshot and snapshot["close"] is not None else "FAILED"
            error = None if status == "OK" else f"缺少 {trade_date} 日终快照"
            if status == "OK":
                ok_symbols.append(symbol)
                _copy_daily_snapshot_to_market_quote(conn, dict(snapshot))
            else:
                missing_symbols.append(symbol)
            conn.execute(
                """
                INSERT INTO post_market_data_prep_items (
                    item_id, run_id, symbol, name, source_type, status,
                    trade_date, error_message, created_at, payload_json
                )
                VALUES (?, ?, ?, ?, 'POSITION', ?, ?, ?, ?, ?)
                """,
                (
                    f"{run_id}_item_{index:04d}_{symbol}",
                    run_id,
                    symbol,
                    position.get("name"),
                    status,
                    trade_date,
                    error,
                    now_iso(),
                    json_text({"mode": "SNAPSHOT_PREPARE", "symbol": symbol, "trade_date": trade_date, "error": error}),
                ),
            )
        valuation_sync = sync_position_valuations(conn, ok_symbols)
        status = "OK" if not missing_symbols else "PARTIAL" if ok_symbols else "FAILED"
        conn.execute(
            """
            UPDATE post_market_data_prep_runs
            SET success_count=?, failed_count=?, position_sync_count=?,
                account_sync_count=?, finished_at=?, status=?, error_message=?,
                payload_json=?
            WHERE run_id=?
            """,
            (
                len(ok_symbols),
                len(missing_symbols),
                (valuation_sync or {}).get("positions") or 0,
                (valuation_sync or {}).get("accounts") or 0,
                now_iso(),
                status,
                None if not missing_symbols else f"缺少 {len(missing_symbols)} 只持仓日终快照",
                json_text(
                    {
                        "mode": "SNAPSHOT_PREPARE",
                        "trade_date": trade_date,
                        "ok_symbols": ok_symbols,
                        "missing_symbols": missing_symbols,
                        "valuation_sync": valuation_sync,
                    }
                ),
                run_id,
            ),
        )
        conn.commit()
    return {
        "run_id": run_id,
        "trade_date": trade_date,
        "total": len(positions),
        "success_count": len(ok_symbols),
        "failed_count": len(missing_symbols),
        "valuation_sync": valuation_sync,
    }


def _copy_daily_snapshot_to_market_quote(conn, snapshot: dict[str, Any]) -> None:
    now = now_iso()
    conn.execute(
        """
        INSERT INTO market_quotes (
            symbol, name, exchange, asset_type, trade_date, quote_time, price,
            pct_change, pe_ttm, pb, market_cap_yuan, ma20, ma60,
            change_20d_pct, source, source_path, updated_at, payload_json
        )
        VALUES (?, ?, ?, COALESCE(?, 'stock'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            name=COALESCE(excluded.name, market_quotes.name),
            exchange=COALESCE(excluded.exchange, market_quotes.exchange),
            asset_type=excluded.asset_type,
            trade_date=excluded.trade_date,
            quote_time=excluded.quote_time,
            price=excluded.price,
            pct_change=excluded.pct_change,
            pe_ttm=excluded.pe_ttm,
            pb=excluded.pb,
            market_cap_yuan=excluded.market_cap_yuan,
            ma20=excluded.ma20,
            ma60=excluded.ma60,
            change_20d_pct=excluded.change_20d_pct,
            source=excluded.source,
            source_path=excluded.source_path,
            updated_at=excluded.updated_at,
            payload_json=excluded.payload_json
        """,
        (
            snapshot.get("symbol"),
            snapshot.get("name"),
            snapshot.get("exchange"),
            snapshot.get("asset_type") or "stock",
            snapshot.get("trade_date"),
            snapshot.get("observed_at"),
            snapshot.get("close"),
            snapshot.get("pct_change"),
            snapshot.get("pe_ttm"),
            snapshot.get("pb"),
            snapshot.get("market_cap_yuan"),
            snapshot.get("ma20"),
            snapshot.get("ma60"),
            snapshot.get("change_20d_pct"),
            "daily_market_snapshots",
            snapshot.get("batch_id"),
            now,
            snapshot.get("payload_json"),
        ),
    )


def read_full_market_codes(settings: DashboardSettings) -> list[dict[str, str]]:
    path = settings.output_root / "沪深A股代码（不含创业板）.csv"
    if not path.exists():
        raise ValueError(f"股票代码文件不存在：{path}")
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            symbol = "".join(ch for ch in str(row.get("股票代码") or "").strip() if ch.isdigit())[:6]
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            rows.append(
                {
                    "symbol": symbol,
                    "name": str(row.get("股票名称") or "").strip(),
                    "exchange": str(row.get("交易所") or "").strip(),
                    "board": str(row.get("板块") or "").strip(),
                }
            )
    if not rows:
        raise ValueError(f"股票代码文件为空：{path}")
    return rows


def create_full_market_data_collection(settings: DashboardSettings, *, limit: int | None = None) -> dict[str, Any]:
    stocks = read_full_market_codes(settings)
    if limit is not None and limit > 0:
        stocks = stocks[:limit]
    started_at = now_iso()
    batch_id = f"marketfull_{compact_now()}"
    with connect(settings.db_path) as conn:
        running = conn.execute(
            """
            SELECT batch_id, total_count
            FROM market_data_batches
            WHERE batch_type='POST_MARKET_FULL' AND status='RUNNING'
            ORDER BY started_at DESC
            LIMIT 1
            """
        ).fetchone()
        if running:
            return {"batch_id": running["batch_id"], "total": running["total_count"], "already_running": True}
        create_market_data_batch(
            conn,
            batch_id=batch_id,
            batch_type="POST_MARKET_FULL",
            session_type="POST_MARKET",
            scope_type="FULL_MARKET",
            scope={"source_file": "data/沪深A股代码（不含创业板）.csv", "count": len(stocks)},
            total_count=len(stocks),
            source="full_market_post_market_collect",
            params={"period": "middle", "limit": limit},
            started_at=started_at,
        )
        for index, stock in enumerate(stocks, start=1):
            conn.execute(
                """
                INSERT INTO market_data_batch_items (
                    item_id, batch_id, symbol, name, exchange, status
                )
                VALUES (?, ?, ?, ?, ?, 'PENDING')
                """,
                (
                    f"{batch_id}_item_{index:05d}_{stock['symbol']}",
                    batch_id,
                    stock["symbol"],
                    stock.get("name"),
                    stock.get("exchange"),
                ),
            )
        conn.commit()
    return {"batch_id": batch_id, "total": len(stocks)}


def run_full_market_data_collection(settings: DashboardSettings, batch_id: str) -> dict[str, Any]:
    with connect(settings.db_path) as conn:
        items = [
            dict(row)
            for row in conn.execute(
                """
                SELECT item_id, symbol, name
                FROM market_data_batch_items
                WHERE batch_id=? AND status IN ('PENDING', 'RUNNING')
                ORDER BY item_id
                """,
                (batch_id,),
            ).fetchall()
        ]
    rows: list[dict[str, Any]] = []
    success_count = 0
    failed_count = 0
    latest_trade_date: str | None = None
    try:
        for item in items:
            symbol = item["symbol"]
            item_started_at = now_iso()
            with connect(settings.db_path) as conn:
                conn.execute(
                    """
                    UPDATE market_data_batch_items
                    SET status='RUNNING', started_at=?
                    WHERE item_id=?
                    """,
                    (item_started_at, item["item_id"]),
                )
                conn.commit()
            try:
                payload = build_payload(symbol, "middle")
                quote = payload.get("quote") or {}
                with connect(settings.db_path) as conn:
                    upsert_market_quote(conn, symbol, payload, source_path="full_market_post_market_collect")
                    upsert_daily_market_snapshot(
                        conn,
                        symbol,
                        payload,
                        batch_id=batch_id,
                        data_origin="LIVE_CAPTURE",
                        source_version="stock_decision_data.middle",
                    )
                    conn.execute(
                        """
                        UPDATE market_data_batch_items
                        SET status='OK', name=COALESCE(?, name), trade_date=?,
                            finished_at=?, error_message=NULL, payload_json=?
                        WHERE item_id=?
                        """,
                        (
                            quote.get("name"),
                            quote.get("trade_date"),
                            now_iso(),
                            json_text(
                                {
                                    "symbol": symbol,
                                    "name": quote.get("name"),
                                    "trade_date": quote.get("trade_date"),
                                }
                            ),
                            item["item_id"],
                        ),
                    )
                    success_count += 1
                    latest_trade_date = max(latest_trade_date or "", str(quote.get("trade_date") or "")) or latest_trade_date
                    _update_market_batch_progress(conn, batch_id, latest_trade_date)
                    conn.commit()
                rows.append({"code": symbol, "status": "OK", "trade_date": quote.get("trade_date")})
            except Exception as exc:  # noqa: BLE001 - keep collection moving.
                failed_count += 1
                with connect(settings.db_path) as conn:
                    conn.execute(
                        """
                        UPDATE market_data_batch_items
                        SET status='FAILED', finished_at=?, error_message=?
                        WHERE item_id=?
                        """,
                        (now_iso(), str(exc), item["item_id"]),
                    )
                    _update_market_batch_progress(conn, batch_id, latest_trade_date)
                    conn.commit()
                rows.append({"code": symbol, "status": "FAILED", "error": str(exc)})
        with connect(settings.db_path) as conn:
            status = "OK" if failed_count == 0 else "PARTIAL" if success_count > 0 else "FAILED"
            finish_market_data_batch(
                conn,
                batch_id=batch_id,
                trade_date=latest_trade_date,
                status=status,
                success_count=success_count,
                failed_count=failed_count,
                payload={"rows_sample": rows[:100], "row_count": len(rows)},
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - mark whole batch failed.
        with connect(settings.db_path) as conn:
            fail_market_data_batch(conn, batch_id=batch_id, error_message=str(exc))
            conn.commit()
        raise
    return {"batch_id": batch_id, "success_count": success_count, "failed_count": failed_count}


def _update_market_batch_progress(conn, batch_id: str, trade_date: str | None) -> None:
    row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN status='OK' THEN 1 ELSE 0 END) AS ok_count,
            SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) AS failed_count
        FROM market_data_batch_items
        WHERE batch_id=?
        """,
        (batch_id,),
    ).fetchone()
    conn.execute(
        """
        UPDATE market_data_batches
        SET trade_date=COALESCE(?, trade_date),
            success_count=?,
            failed_count=?
        WHERE batch_id=?
        """,
        (
            trade_date,
            int(row["ok_count"] or 0),
            int(row["failed_count"] or 0),
            batch_id,
        ),
    )


def create_post_market_close_workflow(settings: DashboardSettings, *, limit: int | None = None) -> dict[str, Any]:
    run_id = f"close_{compact_now()}"
    started_at = now_iso()
    with connect(settings.db_path) as conn:
        running = conn.execute(
            """
            SELECT run_id
            FROM post_market_close_runs
            WHERE status='RUNNING'
            ORDER BY started_at DESC
            LIMIT 1
            """
        ).fetchone()
        if running:
            return {"run_id": running["run_id"], "already_running": True}
        conn.execute(
            """
            INSERT INTO post_market_close_runs (
                run_id, started_at, status, current_step,
                full_market_status, prepare_status, diagnosis_status, watchlist_status,
                params_json
            )
            VALUES (?, ?, 'RUNNING', 'FULL_MARKET', 'PENDING', 'PENDING', 'PENDING', 'PENDING', ?)
            """,
            (run_id, started_at, json.dumps({"limit": limit}, ensure_ascii=False)),
        )
        conn.commit()
    return {"run_id": run_id, "already_running": False}


def run_post_market_close_workflow(settings: DashboardSettings, run_id: str, *, limit: int | None = None) -> dict[str, Any]:
    try:
        _update_close_run(settings, run_id, current_step="FULL_MARKET", full_market_status="RUNNING")
        market = create_full_market_data_collection(settings, limit=limit)
        market_batch_id = market["batch_id"]
        _update_close_run(settings, run_id, market_batch_id=market_batch_id, total_count=market.get("total") or 0)
        if market.get("already_running"):
            _wait_market_batch(settings, market_batch_id)
        else:
            run_full_market_data_collection(settings, market_batch_id)
        market_status = _market_batch_status(settings, market_batch_id)
        _update_close_run(
            settings,
            run_id,
            full_market_status=market_status.get("status") or "OK",
            success_count=market_status.get("success_count") or 0,
            failed_count=market_status.get("failed_count") or 0,
        )

        _update_close_run(settings, run_id, current_step="PREPARE", prepare_status="RUNNING")
        prepared = run_post_market_snapshot_prepare(settings)
        _update_close_run(settings, run_id, prepare_status="OK", prepare_run_id=prepared.get("run_id"))

        _update_close_run(settings, run_id, current_step="DIAGNOSIS", diagnosis_status="RUNNING")
        diagnosis = run_post_market_diagnosis(settings, tab_ids=["all", "decisions"], include_positions=True)
        _update_close_run(
            settings,
            run_id,
            diagnosis_status="OK",
            diagnosis_run_id=diagnosis.get("run_id"),
            next_watch_count=diagnosis.get("next_watch_count") or 0,
        )

        _update_close_run(settings, run_id, current_step="WATCHLIST", watchlist_status="RUNNING")
        synced = sync_next_day_watch_tab(settings, diagnosis.get("run_id"))
        _update_close_run(
            settings,
            run_id,
            status="OK",
            current_step="DONE",
            watchlist_status="OK",
            finished_at=now_iso(),
            payload={"market": market_status, "prepared": prepared, "diagnosis": diagnosis, "watchlist": synced},
        )
        return {"run_id": run_id, "status": "OK"}
    except Exception as exc:  # noqa: BLE001 - persist failure for UI.
        _update_close_run(settings, run_id, status="FAILED", finished_at=now_iso(), error_message=str(exc))
        raise


def sync_next_day_watch_tab(settings: DashboardSettings, run_id: str | None = None) -> dict[str, Any]:
    with connect(settings.db_path) as conn:
        ensure_default_tabs(conn)
        if not run_id:
            row = conn.execute(
                "SELECT run_id FROM next_day_watch_items ORDER BY COALESCE(created_at, '') DESC LIMIT 1"
            ).fetchone()
            run_id = row["run_id"] if row else None
        conn.execute("DELETE FROM watchlist_items WHERE tab_id='next_day_watch'")
        if not run_id:
            conn.commit()
            return {"run_id": None, "synced": 0}
        rows = conn.execute(
            """
            SELECT symbol, COALESCE(name, symbol) AS name
            FROM next_day_watch_items
            WHERE run_id=? AND review_status NOT IN ('IGNORE')
            ORDER BY CASE priority WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 WHEN 'LOW' THEN 3 ELSE 9 END,
                     symbol
            """,
            (run_id,),
        ).fetchall()
        count = 0
        for row in rows:
            count += upsert_watchlist_item(conn, "next_day_watch", row["symbol"], row["name"])
            count += upsert_watchlist_item(conn, "all", row["symbol"], row["name"])
        conn.commit()
    return {"run_id": run_id, "synced": count}


def _update_close_run(settings: DashboardSettings, run_id: str, **fields: Any) -> None:
    allowed = {
        "finished_at",
        "status",
        "current_step",
        "full_market_status",
        "prepare_status",
        "diagnosis_status",
        "watchlist_status",
        "market_batch_id",
        "prepare_run_id",
        "diagnosis_run_id",
        "total_count",
        "success_count",
        "failed_count",
        "next_watch_count",
        "error_message",
    }
    payload = fields.pop("payload", None)
    assignments: list[str] = []
    values: list[Any] = []
    for key, value in fields.items():
        if key in allowed:
            assignments.append(f"{key}=?")
            values.append(value)
    if payload is not None:
        assignments.append("payload_json=?")
        values.append(json_text(payload))
    if not assignments:
        return
    values.append(run_id)
    with connect(settings.db_path) as conn:
        conn.execute(f"UPDATE post_market_close_runs SET {', '.join(assignments)} WHERE run_id=?", tuple(values))
        conn.commit()


def _market_batch_status(settings: DashboardSettings, batch_id: str) -> dict[str, Any]:
    with connect(settings.db_path) as conn:
        row = conn.execute(
            """
            SELECT batch_id, status, total_count, success_count, failed_count, trade_date
            FROM market_data_batches
            WHERE batch_id=?
            """,
            (batch_id,),
        ).fetchone()
    return dict(row) if row else {}


def _wait_market_batch(settings: DashboardSettings, batch_id: str) -> None:
    while True:
        status = _market_batch_status(settings, batch_id).get("status")
        if status != "RUNNING":
            return
        time.sleep(5)


def run_post_market_diagnosis(
    settings: DashboardSettings,
    *,
    tab_ids: list[str],
    include_positions: bool = True,
) -> dict[str, Any]:
    selected_tabs = [str(tab_id).strip() for tab_id in tab_ids if str(tab_id).strip()]
    created_at = now_iso()
    run_id = f"postdiag_{compact_now()}"
    with connect(settings.db_path) as conn:
        codes, source_by_symbol = _collect_scope_codes(conn, tab_ids=selected_tabs, include_positions=include_positions)
    with connect(settings.db_path) as conn:
        position_symbols = {
            row["symbol"]
            for row in conn.execute(
                "SELECT symbol FROM positions WHERE COALESCE(position_status, 'ACTIVE') != 'CLOSED'"
            ).fetchall()
        }
        quote_by_symbol = {
            row["symbol"]: dict(row)
            for row in conn.execute(
                f"""
                SELECT symbol, name, trade_date, close AS price, pct_change, pe_ttm, pb,
                       ma20, ma60, change_20d_pct
                FROM daily_market_snapshots
                WHERE symbol IN ({",".join("?" for _ in codes) if codes else "''"})
                  AND trade_date = (
                      SELECT MAX(trade_date)
                      FROM daily_market_snapshots
                      WHERE symbol IN ({",".join("?" for _ in codes) if codes else "''"})
                  )
                """,
                tuple(codes + codes),
            ).fetchall()
        } if codes else {}
        position_by_symbol = {
            row["symbol"]: dict(row)
            for row in conn.execute(
                """
                SELECT symbol, stop_loss_price, invalidation_point, target_price,
                       buy_logic, unrealized_pnl_pct, position_pct, locked_quantity
                FROM positions
                WHERE COALESCE(position_status, 'ACTIVE') != 'CLOSED'
                """
            ).fetchall()
        }
        trade_date = max(
            (
                str(row.get("trade_date") or "")
                for row in quote_by_symbol.values()
                if row.get("trade_date")
            ),
            default=None,
        )
        results: list[dict[str, Any]] = []
        next_items: list[dict[str, Any]] = []
        for symbol in codes:
            quote = quote_by_symbol.get(symbol) or {"symbol": symbol}
            source_type = source_by_symbol.get(symbol, "WATCHLIST")
            if not quote.get("trade_date") or quote.get("price") is None:
                result = _post_market_result_from_gap(symbol, quote, source_type, "缺少已落库的盘后行情数据")
            elif trade_date and quote.get("trade_date") != trade_date:
                result = _post_market_result_from_gap(
                    symbol,
                    quote,
                    source_type,
                    f"行情日期 {quote.get('trade_date')} 与本次盘后基准日期 {trade_date} 不一致",
                )
            elif symbol in position_symbols:
                result = _post_market_position_result(symbol, quote, position_by_symbol.get(symbol) or {}, source_type)
            else:
                result = _post_market_watchlist_result(symbol, quote, position_symbols, source_type)
            results.append(result)
            if result["category"] != "NEUTRAL":
                next_items.append(result)
        counts = {
            "POSITION_RISK": sum(1 for item in results if item["category"] == "POSITION_RISK"),
            "BUY_CANDIDATE": sum(1 for item in results if item["category"] == "BUY_CANDIDATE"),
            "WATCH_TOMORROW": sum(1 for item in results if item["category"] == "WATCH_TOMORROW"),
            "DATA_GAP": sum(1 for item in results if item["category"] == "DATA_GAP"),
        }
        usable_count = len([item for item in results if item["category"] != "DATA_GAP"])
        next_trade_date = None
        conn.execute(
            """
            INSERT INTO post_market_diagnosis_runs (
                run_id, trade_date, next_trade_date, source_tabs_json, include_positions,
                total_count, success_count, failed_count, position_risk_count,
                buy_candidate_count, watch_count, data_gap_count, created_at, params_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                trade_date,
                next_trade_date,
                json.dumps(selected_tabs, ensure_ascii=False),
                1 if include_positions else 0,
                len(codes),
                usable_count,
                counts["DATA_GAP"],
                counts["POSITION_RISK"],
                counts["BUY_CANDIDATE"],
                counts["WATCH_TOMORROW"],
                counts["DATA_GAP"],
                created_at,
                json.dumps({"tab_ids": selected_tabs, "include_positions": include_positions}, ensure_ascii=False),
            ),
        )
        for index, item in enumerate(results, start=1):
            result_id = f"{run_id}_res_{index:04d}_{item['symbol']}"
            conn.execute(
                """
                INSERT INTO post_market_diagnosis_results (
                    result_id, run_id, symbol, name, source_type, category, priority,
                    score, price, pct_change, trade_date, matched_rules_json,
                    warnings_json, summary, created_at, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result_id,
                    run_id,
                    item["symbol"],
                    item.get("name"),
                    item.get("source_type"),
                    item["category"],
                    item["priority"],
                    item.get("score"),
                    item.get("price"),
                    item.get("pct_change"),
                    item.get("trade_date"),
                    json.dumps(item.get("matched_rules") or [], ensure_ascii=False),
                    json.dumps(item.get("warnings") or [], ensure_ascii=False),
                    item.get("summary"),
                    created_at,
                    json_text(item),
                ),
            )
        for index, item in enumerate(next_items, start=1):
            item_id = f"{run_id}_watch_{index:04d}_{item['symbol']}"
            conn.execute(
                """
                INSERT INTO next_day_watch_items (
                    item_id, run_id, symbol, name, source_type, category, priority,
                    reason, current_price, reference_price, trade_date, next_trade_date,
                    review_status, created_at, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'UNREVIEWED', ?, ?)
                """,
                (
                    item_id,
                    run_id,
                    item["symbol"],
                    item.get("name"),
                    item.get("source_type"),
                    item["category"],
                    item["priority"],
                    item.get("summary"),
                    item.get("price"),
                    item.get("reference_price"),
                    item.get("trade_date"),
                    next_trade_date,
                    created_at,
                    json_text(item),
                ),
            )
        conn.commit()
    return {
        "run_id": run_id,
        "total": len(codes),
        "success_count": len([item for item in results if item["category"] != "DATA_GAP"]),
        "failed_count": len([item for item in results if item["category"] == "DATA_GAP"]),
        "next_watch_count": len(next_items),
    }


def _post_market_result_from_gap(symbol: str, quote: dict[str, Any], source_type: str, reason: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": quote.get("name"),
        "source_type": source_type,
        "category": "DATA_GAP",
        "priority": "HIGH",
        "score": 0,
        "price": quote.get("price"),
        "pct_change": quote.get("pct_change"),
        "trade_date": quote.get("trade_date"),
        "matched_rules": [],
        "warnings": [reason],
        "summary": reason,
    }


def _post_market_watchlist_result(symbol: str, quote: dict[str, Any], position_symbols: set[str], source_type: str) -> dict[str, Any]:
    evaluated = evaluate_watchlist_quote(quote, position_symbols)
    category_map = {
        "CANDIDATE": "BUY_CANDIDATE",
        "WATCH": "WATCH_TOMORROW",
        "RISK": "WATCH_TOMORROW",
        "DATA_GAP": "DATA_GAP",
        "NEUTRAL": "NEUTRAL",
    }
    priority = "HIGH" if evaluated["category"] == "CANDIDATE" else "MEDIUM" if evaluated["category"] in {"WATCH", "RISK", "DATA_GAP"} else "LOW"
    return {
        **evaluated,
        "source_type": source_type,
        "category": category_map.get(evaluated["category"], "NEUTRAL"),
        "priority": priority,
        "reference_price": evaluated.get("ma20"),
    }


def _post_market_position_result(symbol: str, quote: dict[str, Any], position: dict[str, Any], source_type: str) -> dict[str, Any]:
    price = _as_float(quote.get("price"))
    stop_loss = _as_float(position.get("stop_loss_price")) or _as_float(position.get("invalidation_point"))
    target_price = _as_float(position.get("target_price"))
    pnl_pct = _as_float(position.get("unrealized_pnl_pct"))
    rules: list[str] = []
    warnings: list[str] = []
    priority = "LOW"
    category = "WATCH_TOMORROW"
    reference_price = None
    if not str(position.get("buy_logic") or "").strip():
        warnings.append("缺少原买入逻辑")
        priority = "MEDIUM"
    if stop_loss is None:
        warnings.append("缺少证伪点或止损价")
        priority = "HIGH"
        category = "POSITION_RISK"
    elif price is not None:
        reference_price = stop_loss
        if price <= stop_loss:
            warnings.append("已跌破止损/证伪价")
            priority = "HIGH"
            category = "POSITION_RISK"
        elif price <= stop_loss * 1.03:
            warnings.append("接近止损/证伪价")
            priority = "HIGH"
            category = "POSITION_RISK"
    if pnl_pct is not None and pnl_pct <= -8:
        warnings.append("浮亏超过 8%")
        priority = "MEDIUM" if priority == "LOW" else priority
        category = "POSITION_RISK"
    if price is not None and target_price is not None:
        reference_price = target_price
        if price >= target_price:
            rules.append("达到目标价")
            priority = "MEDIUM" if priority == "LOW" else priority
        elif price >= target_price * 0.97:
            rules.append("接近目标价")
            priority = "LOW" if priority == "LOW" else priority
    if not rules and not warnings:
        rules.append("持仓明日继续观察")
    summary = "；".join(warnings + rules)
    return {
        "symbol": symbol,
        "name": quote.get("name"),
        "source_type": source_type,
        "category": category,
        "priority": priority,
        "score": 0,
        "price": price,
        "pct_change": _as_float(quote.get("pct_change")),
        "trade_date": quote.get("trade_date"),
        "reference_price": reference_price,
        "matched_rules": rules,
        "warnings": warnings,
        "summary": summary,
    }


def update_next_day_watch_review(
    settings: DashboardSettings,
    item_id: str,
    status: str,
    note: str | None = None,
) -> dict[str, Any]:
    allowed = {"UNREVIEWED", "VIEWED", "WATCH_INTRADAY", "NEED_DECISION", "DECISION_CREATED", "DONE", "IGNORE", "DEFER"}
    normalized = status.strip().upper()
    if normalized not in allowed:
        raise ValueError(f"invalid next day watch status: {status}")
    with connect(settings.db_path) as conn:
        row = conn.execute("SELECT item_id, run_id FROM next_day_watch_items WHERE item_id=?", (item_id,)).fetchone()
        if row is None:
            raise ValueError("next day watch item not found")
        conn.execute(
            """
            UPDATE next_day_watch_items
            SET review_status=?, review_action=?, reviewed_at=?, review_note=?
            WHERE item_id=?
            """,
            (normalized, normalized, now_iso(), (note or "").strip() or None, item_id),
        )
        conn.commit()
    return {"item_id": item_id, "run_id": row["run_id"], "review_status": normalized}


def generate_decision_from_next_day_watch(settings: DashboardSettings, item_id: str) -> dict[str, Any]:
    with connect(settings.db_path) as conn:
        row = conn.execute(
            "SELECT item_id, run_id, symbol, name FROM next_day_watch_items WHERE item_id=?",
            (item_id,),
        ).fetchone()
        if row is None:
            raise ValueError("next day watch item not found")
    result = _generate_decision_for_symbol(settings, row["symbol"], name=row["name"], source="next_day_watch_decision_refresh")
    with connect(settings.db_path) as conn:
        conn.execute(
            """
            UPDATE next_day_watch_items
            SET review_status='DECISION_CREATED',
                review_action='DECISION_CREATED',
                reviewed_at=?,
                review_note=?,
                decision_id=?
            WHERE item_id=?
            """,
            (
                now_iso(),
                json.dumps(
                    {
                        "task": result.get("task"),
                        "decision_id": result.get("decision_id"),
                        "decision_path": result.get("decision_path"),
                        "report_path": result.get("report_path"),
                    },
                    ensure_ascii=False,
                ),
                result.get("decision_id"),
                item_id,
            ),
        )
        conn.commit()
    return {"run_id": row["run_id"], **result}


def add_watchlist_tab(settings: DashboardSettings, name: str) -> dict[str, Any]:
    with connect(settings.db_path) as conn:
        ensure_default_tabs(conn)
        tab_id = create_watchlist_tab(conn, name)
        conn.commit()
    return {"tab_id": tab_id}


def add_watchlist_stock(settings: DashboardSettings, tab_id: str, symbol: str) -> dict[str, Any]:
    with connect(settings.db_path) as conn:
        ensure_default_tabs(conn)
        count = upsert_watchlist_item(conn, tab_id, symbol)
        conn.commit()
    return {"tab_id": tab_id, "symbol": symbol, "count": count}


def remove_watchlist_stock(settings: DashboardSettings, tab_id: str, symbol: str) -> dict[str, Any]:
    with connect(settings.db_path) as conn:
        removed = remove_watchlist_item(conn, tab_id, symbol)
        conn.commit()
    return {"tab_id": tab_id, "symbol": symbol, "removed": removed}


def delete_user_watchlist_tab(settings: DashboardSettings, tab_id: str) -> dict[str, Any]:
    with connect(settings.db_path) as conn:
        deleted = delete_watchlist_tab(conn, tab_id)
        conn.commit()
    return {"tab_id": tab_id, "deleted": deleted}


def sync_positions_watchlist(settings: DashboardSettings) -> dict[str, Any]:
    with connect(settings.db_path) as conn:
        count = sync_positions_tab(conn)
        conn.commit()
    return {"synced": count}


def bootstrap_watchlists(settings: DashboardSettings) -> dict[str, Any]:
    return bootstrap_watchlist_database(settings.output_root, str(settings.db_path))


def update_position_plan(
    settings: DashboardSettings,
    account_id: str,
    symbol: str,
    *,
    buy_logic: str | None,
    invalidation_point: str | None,
    stop_loss_price: str | None,
    target_price: str | None,
    planned_position_pct: str | None,
    position_note: str | None,
) -> dict[str, Any]:
    def numeric_or_none(value: str | None) -> float | None:
        text = str(value or "").strip()
        if not text:
            return None
        return float(text)

    tracked_fields = [
        "buy_logic",
        "invalidation_point",
        "stop_loss_price",
        "target_price",
        "planned_position_pct",
        "position_note",
    ]
    new_values = {
        "buy_logic": (buy_logic or "").strip() or None,
        "invalidation_point": numeric_or_none(invalidation_point),
        "stop_loss_price": numeric_or_none(stop_loss_price),
        "target_price": numeric_or_none(target_price),
        "planned_position_pct": numeric_or_none(planned_position_pct),
        "position_note": (position_note or "").strip() or None,
    }
    with connect(settings.db_path) as conn:
        row = conn.execute(
            """
            SELECT account_id, symbol, buy_logic, invalidation_point, stop_loss_price,
                   target_price, planned_position_pct, position_note
            FROM positions
            WHERE account_id=? AND symbol=?
            """,
            (account_id, symbol),
        ).fetchone()
        if row is None:
            raise ValueError(f"position not found: {account_id}/{symbol}")
        before_values = {field: row[field] for field in tracked_fields}
        changed_before = {field: before_values[field] for field in tracked_fields if before_values[field] != new_values[field]}
        changed_after = {field: new_values[field] for field in tracked_fields if before_values[field] != new_values[field]}
        updated_at = now_iso()
        conn.execute(
            """
            UPDATE positions
            SET buy_logic = ?,
                invalidation_point = ?,
                stop_loss_price = ?,
                target_price = ?,
                planned_position_pct = ?,
                position_note = ?,
                updated_at = ?
            WHERE account_id = ? AND symbol = ?
            """,
            (
                new_values["buy_logic"],
                new_values["invalidation_point"],
                new_values["stop_loss_price"],
                new_values["target_price"],
                new_values["planned_position_pct"],
                new_values["position_note"],
                updated_at,
                account_id,
                symbol,
            ),
        )
        if changed_after:
            conn.execute(
                """
                INSERT INTO audit_logs (
                    audit_id, account_id, target_type, target_id, operation,
                    before_value_json, after_value_json, reason, operator, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"audit_position_plan_{account_id}_{symbol}_{compact_now()}",
                    account_id,
                    "POSITION",
                    f"{account_id}:{symbol}",
                    "UPDATE_POSITION_PLAN",
                    json_text(changed_before),
                    json_text(changed_after),
                    "web position detail form",
                    "local_user",
                    updated_at,
                ),
            )
        conn.commit()
    return {"account_id": account_id, "symbol": symbol, "changed": bool(changed_after)}


def generate_watchlist_screen(settings: DashboardSettings, tab_id: str, codes: list[str]) -> dict[str, Any]:
    with connect(settings.db_path) as conn:
        result = create_screen_run(conn, tab_id=tab_id, codes=codes, params={"source": "web_selected_codes"})
        conn.commit()
    return result

def update_screen_result_review(
    settings: DashboardSettings,
    result_id: str,
    status: str,
    action: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    allowed = {"UNREVIEWED", "WATCH_TODAY", "IGNORE", "GENERATE_DECISION", "DECISION_CREATED", "DEFER"}
    normalized = status.strip().upper()
    if normalized not in allowed:
        raise ValueError(f"invalid review status: {status}")
    with connect(settings.db_path) as conn:
        row = conn.execute("SELECT run_id FROM watchlist_screen_results WHERE result_id=?", (result_id,)).fetchone()
        if row is None:
            raise ValueError("screen result not found")
        conn.execute(
            """
            UPDATE watchlist_screen_results
            SET review_status=?, review_action=?, reviewed_at=?, review_note=?
            WHERE result_id=?
            """,
            (normalized, action or normalized, now_iso(), note, result_id),
        )
        conn.commit()
        return {"result_id": result_id, "run_id": row["run_id"], "review_status": normalized}


def _generate_decision_for_symbol(
    settings: DashboardSettings,
    symbol: str,
    *,
    name: str | None = None,
    source: str = "decision_refresh",
) -> dict[str, Any]:
    output_root = settings.output_root
    with connect(settings.db_path) as conn:
        ensure_default_tabs(conn)
        payload = build_payload(symbol, "middle")
        upsert_market_quote(conn, symbol, payload, source_path=source)
        sync_position_valuations(conn, [symbol])
        position = conn.execute(
            """
            SELECT account_id, avg_cost, position_pct, total_quantity, available_quantity,
                   buy_logic, invalidation_point, stop_loss_price
            FROM positions
            WHERE symbol=? AND COALESCE(position_status, 'ACTIVE') != 'CLOSED'
            ORDER BY account_id
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()
        strategy_context = get_strategy_context(conn, symbol) or {}
        account_state = None
        if position is not None:
            account_state = conn.execute(
                """
                SELECT available_cash, total_assets, cash_pct
                FROM account_states
                WHERE account_id=?
                ORDER BY COALESCE(updated_at, as_of_time, '') DESC
                LIMIT 1
                """,
                (position["account_id"],),
            ).fetchone()
        conn.commit()

    technical = payload.get("technical") or {}
    fallback_invalidation = (
        position["invalidation_point"]
        if position is not None and position["invalidation_point"] not in (None, "")
        else position["stop_loss_price"]
        if position is not None and position["stop_loss_price"] not in (None, "")
        else technical.get("low_20d")
    )
    user_context = {
        "avg_cost": position["avg_cost"] if position is not None else None,
        "position_pct": position["position_pct"] if position is not None else None,
        "total_quantity": position["total_quantity"] if position is not None else None,
        "available_quantity": position["available_quantity"] if position is not None else None,
        "buy_logic": (
            position["buy_logic"]
            if position is not None and position["buy_logic"] not in (None, "")
            else "真实持仓导入，原买入逻辑未记录" if position is not None else None
        ),
        "invalidation_point": fallback_invalidation if position is not None else None,
        "holding_period": strategy_context.get("holding_period") or "middle",
        "available_cash": account_state["available_cash"] if account_state is not None else None,
        "total_assets": account_state["total_assets"] if account_state is not None else None,
        "cash_reserve_pct": account_state["cash_pct"] if account_state is not None else None,
        "core_logic": strategy_context.get("core_logic") or (
            position["buy_logic"] if position is not None else None
        ),
        "technical_invalidation": strategy_context.get("technical_invalidation") or (
            fallback_invalidation if position is not None else None
        ),
    }
    user_context.update(
        {
            field: strategy_context[field]
            for field in CONTEXT_FIELDS
            if strategy_context.get(field) is not None
        }
    )
    decision_now = datetime.now(_tzinfo())
    trade_date = (payload.get("quote") or {}).get("trade_date")
    registry = build_builtin_registry(
        ai_provider=provider_from_name(settings.ai_provider, model=settings.ai_model),
        # Let the CLI provider enforce and clean up its 120s subprocess timeout
        # before the strategy-level fallback returns BLOCKED.
        ai_timeout_seconds=settings.ai_timeout_seconds + 10,
    )
    pipeline = StrategyPipeline(registry)

    buy_time = build_time_context(
        payload,
        TaskType.BUY.value,
        now=decision_now,
        trade_date_override=trade_date,
    )
    buy_snapshot = build_analysis_snapshot(
        payload,
        buy_time,
        TaskType.BUY,
        user_context=user_context,
        source_file=None,
    )
    buy_result = pipeline.run(buy_snapshot)

    holding_result: PipelineResult | None = None
    if position is not None:
        holding_time = build_time_context(
            payload,
            TaskType.HOLDING.value,
            now=decision_now,
            trade_date_override=trade_date,
        )
        holding_snapshot = build_analysis_snapshot(
            payload,
            holding_time,
            TaskType.HOLDING,
            user_context=user_context,
            source_file=None,
        )
        holding_result = pipeline.run(holding_snapshot)

    stamp = decision_now.strftime("%Y%m%dT%H%M%S%f")
    analysis_id = f"sas_{symbol}_{stamp}"
    result_path = output_root / "strategy_platform" / "runs" / f"strategy_analysis_{symbol}_{stamp}.json"
    report_path = output_root / "reports" / f"strategy_report_{symbol}_{stamp}.md"
    ensure_dir(result_path.parent)
    payload_json = {
        "schema_version": "strategy_analysis_session.v0.2",
        "analysis_id": analysis_id,
        "symbol": buy_snapshot.symbol,
        "name": buy_snapshot.name,
        "trade_date": buy_snapshot.trade_date,
        "decision_time": buy_snapshot.decision_time,
        "source": source,
        "has_position": position is not None,
        "buy": buy_result.to_dict(),
        "holding": holding_result.to_dict() if holding_result is not None else None,
    }
    result_path.write_text(json.dumps(payload_json, ensure_ascii=False, indent=2), encoding="utf-8")
    write_analysis_session_report(report_path, buy_result, holding_result)

    all_results = [buy_result] + ([holding_result] if holding_result is not None else [])
    all_aggregations = [item.aggregation for item in all_results]
    all_runs = [item.run for item in all_results]
    session = {
        "analysis_id": analysis_id,
        "symbol": buy_snapshot.symbol,
        "name": buy_snapshot.name,
        "trade_date": buy_snapshot.trade_date,
        "decision_time": buy_snapshot.decision_time,
        "source": source,
        "has_position": position is not None,
        "buy_snapshot_id": buy_result.snapshot.snapshot_id,
        "buy_run_id": buy_result.run.run_id,
        "buy_aggregation_id": buy_result.aggregation.aggregation_id,
        "buy_conclusion": buy_result.aggregation.conclusion.value,
        "holding_snapshot_id": holding_result.snapshot.snapshot_id if holding_result else None,
        "holding_run_id": holding_result.run.run_id if holding_result else None,
        "holding_aggregation_id": holding_result.aggregation.aggregation_id if holding_result else None,
        "holding_conclusion": holding_result.aggregation.conclusion.value if holding_result else None,
        "effective_strategy_count": sum(item.effective_strategy_count for item in all_aggregations),
        "blocked_strategy_count": sum(len(item.blocked_strategies) for item in all_aggregations),
        "failed_strategy_count": sum(len(item.failed_strategies) for item in all_aggregations),
        "status": _session_status([item.status.value for item in all_runs]),
        "report_relative_path": relative_path(output_root, report_path),
        "payload": payload_json,
    }
    with connect(settings.db_path) as conn:
        save_pipeline_result(conn, buy_result)
        if holding_result is not None:
            save_pipeline_result(conn, holding_result)
        save_analysis_session(conn, session)
        write_report(
            conn,
            output_root,
            report_path,
            report_type="strategy_platform",
            title=f"多策略综合技术报告 {buy_snapshot.symbol} {buy_snapshot.name or ''}".rstrip(),
            symbol=buy_snapshot.symbol,
            trade_date=buy_snapshot.trade_date,
            strategy_version="strategy_platform.v0.2",
            source_type="strategy_analysis_session",
            source_id=analysis_id,
            metadata={"result_path": relative_path(output_root, result_path)},
        )
        upsert_watchlist_item(conn, "decisions", symbol, name or buy_snapshot.name)
        conn.commit()
    return {
        "symbol": symbol,
        "task": "position_multi_strategy" if position is not None else "buy_multi_strategy",
        "decision_id": analysis_id,
        "final_action": _session_action_code(buy_result, holding_result),
        "decision_path": str(result_path),
        "report_path": str(report_path),
    }


def generate_strategy_analysis(settings: DashboardSettings, symbol: str) -> dict[str, Any]:
    normalized = symbol.strip()
    if len(normalized) != 6 or not normalized.isdigit():
        raise ValueError("股票代码必须是 6 位数字")
    return _generate_decision_for_symbol(settings, normalized, source="decisions_page_refresh")


def save_strategy_context_profile(
    settings: DashboardSettings,
    symbol: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    with connect(settings.db_path) as conn:
        result = save_strategy_context(conn, symbol, values, source="WEB")
        conn.commit()
    return result


def _session_status(statuses: list[str]) -> str:
    if statuses and all(item == "FAILED" for item in statuses):
        return "FAILED"
    if any(item in {"FAILED", "PARTIAL"} for item in statuses):
        return "PARTIAL"
    return "COMPLETED"


def _session_action_code(
    buy_result: PipelineResult,
    holding_result: PipelineResult | None,
) -> str:
    buy = buy_result.aggregation.conclusion.value
    if holding_result is None:
        return f"BUY:{buy}"
    return f"HOLDING:{holding_result.aggregation.conclusion.value}|BUY:{buy}"


def generate_decision_from_screen_result(settings: DashboardSettings, result_id: str) -> dict[str, Any]:
    with connect(settings.db_path) as conn:
        row = conn.execute(
            """
            SELECT r.result_id, r.run_id, r.symbol, r.name
            FROM watchlist_screen_results r
            WHERE r.result_id=?
            """,
            (result_id,),
        ).fetchone()
        if row is None:
            raise ValueError("screen result not found")
    result = _generate_decision_for_symbol(settings, row["symbol"], name=row["name"], source="decision_refresh")
    with connect(settings.db_path) as conn:
        conn.execute(
            """
            UPDATE watchlist_screen_results
            SET review_status='DECISION_CREATED',
                review_action='DECISION_CREATED',
                reviewed_at=?,
                review_note=?
            WHERE result_id=?
            """,
            (
                now_iso(),
                json.dumps(
                    {
                        "task": result.get("task"),
                        "decision_id": result.get("decision_id"),
                        "decision_path": result.get("decision_path"),
                        "report_path": result.get("report_path"),
                    },
                    ensure_ascii=False,
                ),
                result_id,
            ),
        )
        conn.commit()
    return {
        "run_id": row["run_id"],
        "symbol": row["symbol"],
        "task": result.get("task"),
        "decision_id": result.get("decision_id"),
        "final_action": result.get("final_action"),
    }


def generate_decision_from_position_check(settings: DashboardSettings, check_id: str) -> dict[str, Any]:
    with connect(settings.db_path) as conn:
        row = conn.execute(
            """
            SELECT check_id, account_id, symbol, name, rule_code, message
            FROM position_pre_market_checks
            WHERE check_id=?
            """,
            (check_id,),
        ).fetchone()
        if row is None:
            raise ValueError("position check not found")
    result = _generate_decision_for_symbol(settings, row["symbol"], name=row["name"], source="position_check_decision_refresh")
    with connect(settings.db_path) as conn:
        conn.execute(
            """
            UPDATE position_pre_market_checks
            SET review_status='DECISION_CREATED',
                review_action='DECISION_CREATED',
                reviewed_at=?,
                review_note=?
            WHERE check_id=?
            """,
            (
                now_iso(),
                json.dumps(
                    {
                        "task": result.get("task"),
                        "decision_id": result.get("decision_id"),
                        "decision_path": result.get("decision_path"),
                        "report_path": result.get("report_path"),
                        "source_rule": row["rule_code"],
                    },
                    ensure_ascii=False,
                ),
                check_id,
            ),
        )
        conn.commit()
    return result
