"""Manual workflow orchestration for pre-market, post-market, and research runs."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .build_decision import build_decision
from .db.sync import mirror_record, mirror_report
from .decision_utils import ensure_dir
from .file_store import append_jsonl, write_json
from .intraday_trigger import run_intraday_scan
from .paper_trading import create_snapshot
from .portfolio import (
    compact_now,
    ensure_account_positions,
    is_weekday_trade_date,
    load_accounts,
    load_positions,
    now_iso,
    rollover,
)
from .portfolio_plan import build_plan
from .timekeeper import build_time_context


WORKFLOW_STATUS_SUCCESS = "SUCCESS"
WORKFLOW_STATUS_FAILED = "FAILED"
WORKFLOW_STATUS_BLOCKED = "BLOCKED"


def workflow_dir(output_root: Path) -> Path:
    return output_root / "workflows"


def workflow_runs_path(output_root: Path) -> Path:
    return workflow_dir(output_root) / "workflow_runs.jsonl"


def reports_dir(output_root: Path) -> Path:
    return output_root / "reports"


def _split_symbols(value: str | None) -> list[str]:
    if not value:
        return []
    symbols: list[str] = []
    for item in value.replace("\n", ",").split(","):
        symbol = item.strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def _load_watchlist_symbols(path: Path | None) -> list[str]:
    if not path or not path.exists():
        return []
    symbols: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        symbol = line.split(",")[0].strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def _stock_symbols_from_dir(data_dir: Path) -> list[str]:
    if not data_dir.exists():
        return []
    symbols: list[str] = []
    for path in sorted(data_dir.glob("stock_data_*.json")):
        symbol = path.stem.replace("stock_data_", "", 1)
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def _resolve_symbols(args: argparse.Namespace) -> list[str]:
    symbols = _split_symbols(getattr(args, "symbols", None))
    symbols.extend(_load_watchlist_symbols(Path(args.watchlist)) if getattr(args, "watchlist", None) else [])
    if not symbols:
        symbols.extend(_stock_symbols_from_dir(Path(args.data_dir)))

    deduped: list[str] = []
    for symbol in symbols:
        if symbol not in deduped:
            deduped.append(symbol)

    limit = getattr(args, "limit", None)
    if limit and limit > 0:
        return deduped[:limit]
    return deduped


def _time_context_for_workflow(trade_date: str | None, workflow_type: str) -> dict[str, Any]:
    raw_data = {"quote": {"trade_date": trade_date, "market": "A_STOCK"}}
    return build_time_context(raw_data, workflow_type)


def _start_run(workflow_type: str, trade_date: str | None, params: dict[str, Any]) -> dict[str, Any]:
    time_context = _time_context_for_workflow(trade_date, workflow_type)
    return {
        "workflow_run_id": f"wf_{workflow_type}_{compact_now()}",
        "workflow_type": workflow_type,
        "trade_date": trade_date,
        "calendar_date": time_context.get("calendar_date"),
        "session_name": time_context.get("session_name"),
        "is_trading_day": time_context.get("is_trading_day"),
        "effective_data_cutoff": time_context.get("effective_data_cutoff"),
        "started_at": now_iso(),
        "finished_at": None,
        "status": None,
        "input_params_json": params,
        "output_refs_json": {},
        "error_code": None,
        "error_message": None,
        "created_at": now_iso(),
    }


def _finish_run(
    output_root: Path,
    run: dict[str, Any],
    status: str,
    outputs: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    run["finished_at"] = now_iso()
    run["status"] = status
    run["output_refs_json"] = outputs or {}
    run["error_code"] = error_code
    run["error_message"] = error_message
    path = workflow_runs_path(output_root)
    append_jsonl(path, run)
    mirror_record(output_root, "workflow_run", run, source_path=path)
    return run


def _base_decision_args(args: argparse.Namespace, account: dict[str, Any] | None = None) -> argparse.Namespace:
    return argparse.Namespace(
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        trade_date=getattr(args, "trade_date", None),
        report=True,
        keep_outputs=args.keep_outputs,
        avg_cost=None,
        position_pct=None,
        total_quantity=None,
        available_quantity=None,
        buy_logic=None,
        invalidation_point=None,
        holding_period=None,
        available_cash=(account or {}).get("available_cash"),
        total_assets=(account or {}).get("total_assets"),
        cash_reserve_pct=(account or {}).get("cash_reserve_pct"),
    )


def _build_buy_decision(symbol: str, args: argparse.Namespace, account: dict[str, Any]) -> dict[str, Any]:
    decision_args = _base_decision_args(args, account)
    return _safe_build_decision(symbol, "buy", decision_args)


def _build_holding_decision(
    symbol: str,
    position: dict[str, Any],
    args: argparse.Namespace,
    account: dict[str, Any],
) -> dict[str, Any]:
    decision_args = _base_decision_args(args, account)
    decision_args.avg_cost = position.get("avg_cost")
    decision_args.position_pct = position.get("position_pct")
    decision_args.total_quantity = position.get("total_quantity")
    decision_args.available_quantity = position.get("available_quantity")
    decision_args.buy_logic = position.get("buy_logic")
    decision_args.invalidation_point = position.get("invalidation_point")
    return _safe_build_decision(symbol, "sell", decision_args)


def _safe_build_decision(symbol: str, task: str, decision_args: argparse.Namespace) -> dict[str, Any]:
    stock_path = Path(decision_args.data_dir) / f"stock_data_{symbol}.json"
    if not stock_path.exists():
        return {
            "symbol": symbol,
            "task": task,
            "status": "SKIPPED",
            "error_code": "STOCK_JSON_MISSING",
            "error_message": str(stock_path),
        }
    try:
        result = build_decision(symbol, task, decision_args)
        decision = result["decision"]
        return {
            "symbol": symbol,
            "task": task,
            "status": "SUCCESS",
            "decision_path": result.get("decision_path"),
            "snapshot_path": result.get("snapshot_path"),
            "report_path": result.get("report_path"),
            "decision_id": decision.get("decision_id"),
            "task_type": decision.get("task_type"),
            "final_action": decision.get("final_action"),
            "confidence": decision.get("confidence"),
            "time_context": decision.get("time_context"),
            "trigger_prices": decision.get("trigger_prices") or {},
            "decision": decision,
        }
    except Exception as exc:  # noqa: BLE001 - workflow should keep per-symbol failures visible.
        return {
            "symbol": symbol,
            "task": task,
            "status": "FAILED",
            "error_code": exc.__class__.__name__,
            "error_message": str(exc),
        }


def _active_positions(output_root: Path, account_id: str) -> dict[str, Any]:
    positions = load_positions(output_root)
    account_positions = ensure_account_positions(positions, account_id)
    return {
        symbol: position
        for symbol, position in account_positions.items()
        if position.get("position_status") != "CLOSED" and int(position.get("total_quantity") or 0) > 0
    }


def _compact_decision_result(result: dict[str, Any]) -> dict[str, Any]:
    compact = dict(result)
    compact.pop("decision", None)
    return compact


def _write_trigger_price_list(
    output_root: Path,
    account_id: str,
    trade_date: str,
    decision_results: list[dict[str, Any]],
) -> Path:
    records: list[dict[str, Any]] = []
    for result in decision_results:
        if result.get("status") != "SUCCESS":
            continue
        time_context = result.get("time_context") or {}
        records.append(
            {
                "symbol": result.get("symbol"),
                "task": result.get("task"),
                "task_type": result.get("task_type"),
                "decision_id": result.get("decision_id"),
                "final_action": result.get("final_action"),
                "confidence": result.get("confidence"),
                "trade_date": time_context.get("trade_date"),
                "session_name": time_context.get("session_name"),
                "source_decision_path": result.get("decision_path"),
                "trigger_prices": result.get("trigger_prices") or {},
            }
        )
    path = workflow_dir(output_root) / f"trigger_price_list_{account_id}_{trade_date}.json"
    payload = {"account_id": account_id, "trade_date": trade_date, "items": records, "created_at": now_iso()}
    write_json(path, payload)
    mirror_record(output_root, "trigger_price_list", payload, source_path=path)
    return path


def _write_pre_market_plan(
    output_root: Path,
    account_id: str,
    trade_date: str,
    run: dict[str, Any],
    symbols: list[str],
    rollover_result: dict[str, Any],
    buy_results: list[dict[str, Any]],
    holding_results: list[dict[str, Any]],
    allocation_result: dict[str, Any] | None,
    trigger_path: Path,
) -> Path:
    path = workflow_dir(output_root) / f"pre_market_plan_{account_id}_{trade_date}.json"
    payload = {
        "workflow_run_id": run["workflow_run_id"],
        "account_id": account_id,
        "trade_date": trade_date,
        "session_name": run.get("session_name"),
        "execution_allowed": bool(allocation_result and allocation_result["allocation_plan"].get("status") == "READY_FOR_CONFIRM"),
        "symbols": symbols,
        "rollover": {
            "trade_date": rollover_result.get("trade_date"),
            "released_quantity": rollover_result.get("released_quantity"),
        },
        "buy_decisions": [_compact_decision_result(result) for result in buy_results],
        "holding_reviews": [_compact_decision_result(result) for result in holding_results],
        "allocation_plan": (allocation_result or {}).get("allocation_plan"),
        "order_intents": (allocation_result or {}).get("order_intents") or [],
        "trigger_price_list_path": str(trigger_path),
        "created_at": now_iso(),
    }
    write_json(path, payload)
    mirror_record(output_root, "pre_market_plan", payload, source_path=path)
    return path


def _write_pre_market_report(
    output_root: Path,
    account_id: str,
    trade_date: str,
    run: dict[str, Any],
    plan_path: Path,
    trigger_path: Path,
    buy_results: list[dict[str, Any]],
    holding_results: list[dict[str, Any]],
    allocation_result: dict[str, Any] | None,
) -> Path:
    ensure_dir(reports_dir(output_root))
    path = reports_dir(output_root) / f"pre_market_report_{account_id}_{trade_date}.md"
    ready = []
    record_only = []
    rejected = []
    if allocation_result:
        for intent in allocation_result["order_intents"]:
            if intent.get("status") == "READY_FOR_CONFIRM":
                ready.append(intent)
            elif intent.get("status") == "REJECTED":
                rejected.append(intent)
            else:
                record_only.append(intent)

    failed = [result for result in buy_results + holding_results if result.get("status") in {"FAILED", "SKIPPED"}]
    lines = [
        f"# 盘前工作流报告 {account_id} {trade_date}",
        "",
        "## 运行摘要",
        f"- 工作流：`{run.get('workflow_run_id')}`",
        f"- 当前时段：`{run.get('session_name')}`",
        f"- 是否当前交易日：{run.get('is_trading_day')}",
        f"- 买入扫描数量：{len(buy_results)}",
        f"- 持仓复核数量：{len(holding_results)}",
        f"- 可确认买入意图：{len(ready)}",
        f"- 只记录/延后候选：{len(record_only)}",
        f"- 拒绝候选：{len(rejected)}",
        f"- 数据失败/跳过：{len(failed)}",
        "",
        "## 可确认买入计划",
    ]
    if not ready:
        lines.append("- 当前没有可确认买入计划。")
    for intent in ready:
        lines.append(
            "- "
            f"{intent.get('symbol')} {intent.get('name')} "
            f"金额={intent.get('planned_cash_amount')} "
            f"数量={intent.get('planned_quantity')} "
            f"价格={intent.get('reference_price')} "
            f"分数={intent.get('score')}"
        )

    lines.extend(["", "## 持仓复核"])
    if not holding_results:
        lines.append("- 当前没有需要复核的持仓。")
    for result in holding_results:
        lines.append(
            "- "
            f"{result.get('symbol')} `{result.get('status')}` "
            f"结论={result.get('final_action')} "
            f"信心={result.get('confidence')} "
            f"文件={result.get('decision_path') or result.get('error_message')}"
        )

    lines.extend(["", "## 数据问题"])
    if not failed:
        lines.append("- 无。")
    for result in failed:
        lines.append(
            "- "
            f"{result.get('symbol')} {result.get('task')} `{result.get('status')}` "
            f"{result.get('error_code')}: {result.get('error_message')}"
        )

    lines.extend(
        [
            "",
            "## 输出文件",
            f"- 盘前计划：{plan_path}",
            f"- 触发价列表：{trigger_path}",
            f"- 组合计划报告：{(allocation_result or {}).get('report_path')}",
            "",
            "## 说明",
            "- 本工作流只生成计划和报告，不会直接成交。",
            "- 是否能成交仍取决于交易日、T+1、价格、现金和人工确认。",
            "- 如果当前时段不是交易日，结果只能作为研究或下一交易日准备。",
            f"- 生成时间：{now_iso()}",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    mirror_report(
        output_root,
        path,
        report_type="pre_market",
        account_id=account_id,
        trade_date=trade_date,
        source_type="workflow_run",
        source_id=run.get("workflow_run_id"),
    )
    return path


def pre_market(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_dir)
    run = _start_run(
        "pre_market",
        args.trade_date,
        {
            "account": args.account,
            "trade_date": args.trade_date,
            "symbols": args.symbols,
            "watchlist": args.watchlist,
            "limit": args.limit,
        },
    )
    try:
        if not is_weekday_trade_date(args.trade_date):
            outputs = {"reason": "trade_date is not a weekday trading candidate"}
            _finish_run(output_root, run, WORKFLOW_STATUS_BLOCKED, outputs, "NON_TRADING_DATE", outputs["reason"])
            return {"run": run, **outputs}

        accounts = load_accounts(output_root)
        account = accounts.get(args.account)
        if not account:
            raise ValueError(f"account not found: {args.account}")

        symbols = _resolve_symbols(args)
        rollover_result = rollover(output_root, args.account, args.trade_date)
        buy_results = [_build_buy_decision(symbol, args, rollover_result["account"]) for symbol in symbols]

        holding_results: list[dict[str, Any]] = []
        if not args.skip_holdings:
            for symbol, position in _active_positions(output_root, args.account).items():
                holding_results.append(_build_holding_decision(symbol, position, args, rollover_result["account"]))

        buy_decision_paths = [result["decision_path"] for result in buy_results if result.get("status") == "SUCCESS"]
        allocation_result = None
        if buy_decision_paths:
            allocation_result = build_plan(
                argparse.Namespace(
                    output_dir=args.output_dir,
                    account=args.account,
                    trade_date=args.trade_date,
                    decision_dir=str(Path(args.output_dir) / "decision_results"),
                    decision_path=buy_decision_paths,
                    snapshot_dir=str(Path(args.output_dir) / "strategy_snapshots"),
                    include=None,
                    strategy_version=args.strategy_version,
                    max_candidates=args.max_candidates,
                    default_watch_cash=args.default_watch_cash,
                    risk_config=args.risk_config,
                )
            )

        trigger_path = _write_trigger_price_list(output_root, args.account, args.trade_date, buy_results + holding_results)
        plan_path = _write_pre_market_plan(
            output_root,
            args.account,
            args.trade_date,
            run,
            symbols,
            rollover_result,
            buy_results,
            holding_results,
            allocation_result,
            trigger_path,
        )
        report_path = _write_pre_market_report(
            output_root,
            args.account,
            args.trade_date,
            run,
            plan_path,
            trigger_path,
            buy_results,
            holding_results,
            allocation_result,
        )
        outputs = {
            "pre_market_plan_path": str(plan_path),
            "trigger_price_list_path": str(trigger_path),
            "pre_market_report_path": str(report_path),
            "allocation_report_path": (allocation_result or {}).get("report_path"),
            "buy_decision_count": len(buy_results),
            "holding_review_count": len(holding_results),
        }
        _finish_run(output_root, run, WORKFLOW_STATUS_SUCCESS, outputs)
        return {"run": run, **outputs}
    except Exception as exc:
        _finish_run(output_root, run, WORKFLOW_STATUS_FAILED, {}, exc.__class__.__name__, str(exc))
        raise


def _write_post_market_report(
    output_root: Path,
    account_id: str,
    trade_date: str,
    run: dict[str, Any],
    snapshot_result: dict[str, Any],
) -> Path:
    ensure_dir(reports_dir(output_root))
    path = reports_dir(output_root) / f"post_market_report_{account_id}_{trade_date}.md"
    account_snapshot = snapshot_result["account_snapshot"]
    position_snapshots = snapshot_result["position_snapshots"]
    lines = [
        f"# 盘后工作流报告 {account_id} {trade_date}",
        "",
        "## 账户快照",
        f"- 工作流：`{run.get('workflow_run_id')}`",
        f"- 总资产：{account_snapshot.get('total_assets')}",
        f"- 可用现金：{account_snapshot.get('available_cash')}",
        f"- 持仓市值：{account_snapshot.get('market_value')}",
        f"- 当日盈亏：{account_snapshot.get('daily_pnl')}",
        f"- 累计收益率：{account_snapshot.get('total_return_pct')}%",
        f"- 最大回撤：{account_snapshot.get('max_drawdown_pct')}%",
        "",
        "## 持仓快照",
    ]
    if not position_snapshots:
        lines.append("- 当前无持仓。")
    for position in position_snapshots:
        lines.append(
            "- "
            f"{position.get('symbol')} "
            f"数量={position.get('total_quantity')} "
            f"可卖={position.get('available_quantity')} "
            f"成本={position.get('avg_cost')} "
            f"现价={position.get('market_price')} "
            f"浮盈亏={position.get('unrealized_pnl')}"
        )
    lines.extend(
        [
            "",
            "## 输出文件",
            f"- 模拟盘报告：{snapshot_result.get('report_path')}",
            "",
            "## 说明",
            "- 盘后工作流用于归档账户和持仓状态。",
            "- 重复运行同一账户同一交易日快照时，会保留最新快照，避免历史污染。",
            f"- 生成时间：{now_iso()}",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    mirror_report(
        output_root,
        path,
        report_type="post_market",
        account_id=account_id,
        trade_date=trade_date,
        source_type="workflow_run",
        source_id=run.get("workflow_run_id"),
    )
    return path


def post_market(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_dir)
    run = _start_run(
        "post_market",
        args.trade_date,
        {"account": args.account, "trade_date": args.trade_date},
    )
    try:
        if not is_weekday_trade_date(args.trade_date):
            outputs = {"reason": "trade_date is not a weekday trading candidate"}
            _finish_run(output_root, run, WORKFLOW_STATUS_BLOCKED, outputs, "NON_TRADING_DATE", outputs["reason"])
            return {"run": run, **outputs}

        snapshot_result = create_snapshot(
            argparse.Namespace(output_dir=args.output_dir, account=args.account, trade_date=args.trade_date)
        )
        report_path = _write_post_market_report(output_root, args.account, args.trade_date, run, snapshot_result)
        outputs = {
            "account_snapshot_id": snapshot_result["account_snapshot"].get("snapshot_id"),
            "position_snapshot_count": len(snapshot_result["position_snapshots"]),
            "paper_report_path": snapshot_result.get("report_path"),
            "post_market_report_path": str(report_path),
        }
        _finish_run(output_root, run, WORKFLOW_STATUS_SUCCESS, outputs)
        return {"run": run, **outputs}
    except Exception as exc:
        _finish_run(output_root, run, WORKFLOW_STATUS_FAILED, {}, exc.__class__.__name__, str(exc))
        raise


def _write_research_report(
    output_root: Path,
    trade_date: str | None,
    run: dict[str, Any],
    decision_results: list[dict[str, Any]],
) -> Path:
    ensure_dir(reports_dir(output_root))
    date_part = trade_date or run.get("calendar_date") or "unknown"
    path = reports_dir(output_root) / f"research_report_{date_part}.md"
    lines = [
        f"# 非交易研究报告 {date_part}",
        "",
        "## 运行摘要",
        f"- 工作流：`{run.get('workflow_run_id')}`",
        f"- 当前时段：`{run.get('session_name')}`",
        "- 执行权限：false",
        f"- 扫描数量：{len(decision_results)}",
        "",
        "## 结果",
    ]
    if not decision_results:
        lines.append("- 没有生成研究结果。")
    for result in decision_results:
        lines.append(
            "- "
            f"{result.get('symbol')} `{result.get('status')}` "
            f"结论={result.get('final_action')} "
            f"信心={result.get('confidence')} "
            f"文件={result.get('decision_path') or result.get('error_message')}"
        )
    lines.extend(
        [
            "",
            "## 说明",
            "- 本报告只用于研究和整理候选，不生成模拟成交。",
            "- 当前阶段不允许把研究结论直接当成交易指令。",
            f"- 生成时间：{now_iso()}",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    mirror_report(
        output_root,
        path,
        report_type="research",
        trade_date=date_part,
        source_type="workflow_run",
        source_id=run.get("workflow_run_id"),
    )
    return path


def research(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_dir)
    run = _start_run(
        "non_trading_research",
        args.trade_date,
        {"symbols": args.symbols, "watchlist": args.watchlist, "task": args.task, "limit": args.limit},
    )
    try:
        account = None
        if args.account:
            account = load_accounts(output_root).get(args.account)
        symbols = _resolve_symbols(args)
        base_args = _base_decision_args(args, account)
        task = "sell" if args.task == "sell" else "buy"
        decision_results = [_safe_build_decision(symbol, task, base_args) for symbol in symbols]
        report_path = _write_research_report(output_root, args.trade_date, run, decision_results)
        outputs = {
            "research_report_path": str(report_path),
            "decision_count": len(decision_results),
            "execution_allowed": False,
        }
        _finish_run(output_root, run, WORKFLOW_STATUS_SUCCESS, outputs)
        return {"run": run, **outputs}
    except Exception as exc:
        _finish_run(output_root, run, WORKFLOW_STATUS_FAILED, {}, exc.__class__.__name__, str(exc))
        raise


def intraday(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_dir)
    run = _start_run(
        "intraday",
        args.trade_date,
        {
            "account": args.account,
            "trade_date": args.trade_date,
            "symbols": args.symbols,
            "plan_path": args.plan_path,
            "trigger_path": args.trigger_path,
            "data_dir": args.data_dir,
            "allow_non_trading": args.allow_non_trading,
        },
    )
    try:
        result = run_intraday_scan(args)
        scan = result["scan"]
        status = WORKFLOW_STATUS_BLOCKED if scan.get("status") == "BLOCKED" else WORKFLOW_STATUS_SUCCESS
        outputs = {
            "intraday_scan_id": scan.get("scan_id"),
            "intraday_scan_status": scan.get("status"),
            "trigger_count": scan.get("trigger_count"),
            "duplicate_count": scan.get("duplicate_count"),
            "blocked_count": scan.get("blocked_count"),
            "intraday_report_path": result.get("report_path"),
        }
        _finish_run(output_root, run, status, outputs, scan.get("error_code"), scan.get("error_message"))
        return {"run": run, **outputs}
    except Exception as exc:
        _finish_run(output_root, run, WORKFLOW_STATUS_FAILED, {}, exc.__class__.__name__, str(exc))
        raise


def _print_result(result: dict[str, Any]) -> None:
    run = result["run"]
    print(f"workflow: {run['workflow_run_id']} status={run['status']}")
    for key, value in result.items():
        if key == "run":
            continue
        print(f"{key}: {value}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manual workflow orchestration for the AI trader project.")
    parser.add_argument("--output-dir", default="data")
    subparsers = parser.add_subparsers(dest="command", required=True)

    pre = subparsers.add_parser("pre-market", help="Run daily pre-market preparation.")
    pre.add_argument("--account", default="paper_default")
    pre.add_argument("--trade-date", required=True)
    pre.add_argument("--symbols")
    pre.add_argument("--watchlist")
    pre.add_argument("--limit", type=int)
    pre.add_argument("--data-dir", default="data/stock_json")
    pre.add_argument("--keep-outputs", type=int, default=5)
    pre.add_argument("--skip-holdings", action="store_true")
    pre.add_argument("--strategy-version", default="strategy_v0.1")
    pre.add_argument("--max-candidates", type=int)
    pre.add_argument("--default-watch-cash", type=float, default=5000.0)
    pre.add_argument("--risk-config")
    pre.set_defaults(func=pre_market)

    post = subparsers.add_parser("post-market", help="Run post-market snapshot and report.")
    post.add_argument("--account", default="paper_default")
    post.add_argument("--trade-date", required=True)
    post.set_defaults(func=post_market)

    research_parser = subparsers.add_parser("research", help="Run non-trading research scan.")
    research_parser.add_argument("--trade-date")
    research_parser.add_argument("--symbols")
    research_parser.add_argument("--watchlist")
    research_parser.add_argument("--limit", type=int)
    research_parser.add_argument("--data-dir", default="data/stock_json")
    research_parser.add_argument("--keep-outputs", type=int, default=5)
    research_parser.add_argument("--account")
    research_parser.add_argument("--task", choices=["buy", "sell"], default="buy")
    research_parser.set_defaults(func=research)

    intraday_parser = subparsers.add_parser("intraday", help="Run intraday trigger scan.")
    intraday_parser.add_argument("--account", default="paper_default")
    intraday_parser.add_argument("--trade-date", required=True)
    intraday_parser.add_argument("--symbols")
    intraday_parser.add_argument("--plan-path")
    intraday_parser.add_argument("--trigger-path")
    intraday_parser.add_argument("--data-dir", default="data/stock_json")
    intraday_parser.add_argument("--allow-non-trading", action="store_true")
    intraday_parser.set_defaults(func=intraday)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = args.func(args)
    _print_result(result)


if __name__ == "__main__":
    main()
