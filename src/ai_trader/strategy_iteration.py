"""Strategy iteration records built from replay and paper-trading results."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any

from .decision_utils import ensure_dir
from .file_store import append_jsonl, load_json, read_jsonl
from .performance import calculate_replay_performance
from .portfolio import (
    accounts_path,
    account_snapshots_path,
    closed_positions_path,
    compact_now,
    now_iso,
)


def iteration_dir(output_root: Path) -> Path:
    return output_root / "strategy_iterations"


def records_path(output_root: Path) -> Path:
    return iteration_dir(output_root) / "strategy_tuning_records.jsonl"


def reports_dir(output_root: Path) -> Path:
    return output_root / "reports"


def report_path(output_root: Path) -> Path:
    return reports_dir(output_root) / "strategy_iteration_report.md"


def _split_values(value: str | None) -> list[str]:
    if not value:
        return []
    items: list[str] = []
    normalized = value.replace("\n", ",").replace("；", ",").replace(";", ",")
    for item in normalized.split(","):
        text = item.strip()
        if text and text not in items:
            items.append(text)
    return items


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _load_json_object(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    data = load_json(path, default or {})
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def _metric_subset(metrics: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "start_date",
        "end_date",
        "initial_cash",
        "final_assets",
        "total_return_pct",
        "annualized_return_pct",
        "max_drawdown_pct",
        "max_drawdown_start",
        "max_drawdown_end",
        "trade_count",
        "buy_count",
        "sell_count",
        "win_rate",
        "profit_loss_ratio",
        "cash_usage_pct",
        "turnover_rate",
        "benchmark_return_pct",
        "excess_return_pct",
    ]
    return {key: metrics.get(key) for key in keys}


def _top_blocked_reasons(records: list[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for record in records:
        for reason in record.get("blocked_reasons") or []:
            counter[str(reason)] += 1
    return dict(counter.most_common(10))


def _worst_days(records: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    sortable = []
    for record in records:
        daily_pnl = _as_float(record.get("daily_pnl"))
        if daily_pnl is None:
            continue
        sortable.append(record)
    sortable.sort(key=lambda item: _as_float(item.get("daily_pnl")) or 0.0)
    return [
        {
            "trade_date": item.get("trade_date"),
            "daily_pnl": item.get("daily_pnl"),
            "total_assets": item.get("total_assets"),
            "trades": item.get("trades"),
            "blocked_reasons": item.get("blocked_reasons") or [],
        }
        for item in sortable[:limit]
    ]


def _symbols_from_trades(trades: list[dict[str, Any]]) -> list[str]:
    symbols: list[str] = []
    for trade in trades:
        symbol = trade.get("symbol")
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def _auto_issues(metrics: dict[str, Any], blocked_counts: dict[str, int]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    trade_count = _as_int(metrics.get("trade_count"))
    total_return_pct = _as_float(metrics.get("total_return_pct"))
    excess_return_pct = _as_float(metrics.get("excess_return_pct"))
    max_drawdown_pct = _as_float(metrics.get("max_drawdown_pct"))
    win_rate = _as_float(metrics.get("win_rate"))
    blocked_total = sum(blocked_counts.values())

    if trade_count == 0:
        issues.append({"code": "NO_TRADES", "severity": "HIGH", "message": "回放或模拟盘没有成交，策略无法评价。"})
    elif trade_count < 5:
        issues.append({"code": "SAMPLE_TOO_SMALL", "severity": "MEDIUM", "message": "交易次数少于 5 次，样本不足。"})
    if total_return_pct is not None and total_return_pct < 0:
        issues.append({"code": "NEGATIVE_RETURN", "severity": "HIGH", "message": "总收益率为负，需要复盘亏损来源。"})
    if excess_return_pct is not None and excess_return_pct < 0:
        issues.append({"code": "UNDERPERFORM_BENCHMARK", "severity": "MEDIUM", "message": "跑输基准。"})
    if max_drawdown_pct is not None and max_drawdown_pct <= -10:
        issues.append({"code": "DRAWDOWN_OVER_10PCT", "severity": "HIGH", "message": "最大回撤超过 10%。"})
    if win_rate is not None and win_rate < 40:
        issues.append({"code": "LOW_WIN_RATE", "severity": "MEDIUM", "message": "胜率低于 40%。"})
    if blocked_total > 10:
        issues.append({"code": "MANY_BLOCKED_REASONS", "severity": "MEDIUM", "message": "阻塞事件较多，需要检查执行规则。"})
    return issues


def _replay_source(args: argparse.Namespace, output_root: Path) -> dict[str, Any]:
    if not args.source_path and not args.replay_id:
        raise ValueError("replay source requires --source-path or --replay-id")
    source_path = Path(args.source_path) if args.source_path else output_root / "replay" / args.replay_id
    if not source_path.exists():
        raise FileNotFoundError(f"replay source path not found: {source_path}")

    config = _load_json_object(source_path / "replay_config.json")
    metrics = _load_json_object(source_path / "performance_metrics.json")
    daily_records = read_jsonl(source_path / "daily_replay_records.jsonl")
    error_cases = read_jsonl(source_path / "error_cases.jsonl")
    trades = read_jsonl(source_path / "paper_trading" / "trades.jsonl")
    blocked_counts = _top_blocked_reasons(daily_records)

    return {
        "source_type": "replay",
        "source_path": str(source_path),
        "source_id": config.get("replay_id") or source_path.name,
        "account_id": config.get("account_id"),
        "strategy_version": config.get("strategy_version"),
        "symbols": config.get("symbols") or _symbols_from_trades(trades),
        "period_start": metrics.get("start_date") or config.get("start_date"),
        "period_end": metrics.get("end_date") or config.get("end_date"),
        "metrics": _metric_subset(metrics),
        "blocked_reason_counts": blocked_counts,
        "worst_days": _worst_days(daily_records),
        "error_case_count": len(error_cases),
        "trade_sample": trades[-5:],
    }


def _paper_source(args: argparse.Namespace, output_root: Path) -> dict[str, Any]:
    account_id = args.account
    if not account_id:
        raise ValueError("--account is required for paper source")

    accounts = _load_json_object(accounts_path(output_root))
    account = accounts.get(account_id)
    if not account:
        raise ValueError(f"account not found: {account_id}")

    initial_cash = float(account.get("initial_cash") or 0.0)
    metrics = calculate_replay_performance(output_root, account_id=account_id, initial_cash=initial_cash)
    snapshots = [
        item
        for item in read_jsonl(account_snapshots_path(output_root))
        if item.get("account_id") == account_id
    ]
    trades = [
        item
        for item in read_jsonl(output_root / "paper_trading" / "trades.jsonl")
        if item.get("account_id") == account_id
    ]
    closed_positions = [
        item
        for item in read_jsonl(closed_positions_path(output_root))
        if item.get("account_id") == account_id
    ]

    daily_records = [
        {
            "trade_date": item.get("trade_date"),
            "daily_pnl": item.get("daily_pnl"),
            "total_assets": item.get("total_assets"),
            "trades": None,
            "blocked_reasons": [],
        }
        for item in snapshots
    ]

    return {
        "source_type": "paper",
        "source_path": str(output_root),
        "source_id": account_id,
        "account_id": account_id,
        "strategy_version": args.strategy_version,
        "symbols": _symbols_from_trades(trades),
        "period_start": metrics.get("start_date"),
        "period_end": metrics.get("end_date"),
        "metrics": _metric_subset(metrics),
        "blocked_reason_counts": {},
        "worst_days": _worst_days(daily_records),
        "error_case_count": 0,
        "closed_position_count": len(closed_positions),
        "trade_sample": trades[-5:],
    }


def _build_record(args: argparse.Namespace, output_root: Path) -> dict[str, Any]:
    if args.source_type == "replay":
        source = _replay_source(args, output_root)
    elif args.source_type == "paper":
        source = _paper_source(args, output_root)
    else:
        raise ValueError(f"unsupported source type: {args.source_type}")

    strategy_version = args.strategy_version or source.get("strategy_version") or "unknown"
    metrics = source.get("metrics") or {}
    blocked_counts = source.get("blocked_reason_counts") or {}
    auto_issues = _auto_issues(metrics, blocked_counts)
    manual_issues = _split_values(args.issues)

    return {
        "iteration_id": f"iter_{compact_now()}",
        "created_at": now_iso(),
        "source_type": source.get("source_type"),
        "source_id": source.get("source_id"),
        "source_path": source.get("source_path"),
        "strategy_version": strategy_version,
        "previous_strategy_version": args.previous_strategy_version,
        "account_id": source.get("account_id"),
        "symbols": source.get("symbols") or _split_values(args.symbols),
        "period_start": source.get("period_start"),
        "period_end": source.get("period_end"),
        "metrics": metrics,
        "auto_issues": auto_issues,
        "manual_issues": manual_issues,
        "blocked_reason_counts": blocked_counts,
        "worst_days": source.get("worst_days") or [],
        "error_case_count": source.get("error_case_count"),
        "closed_position_count": source.get("closed_position_count"),
        "hypothesis": args.hypothesis,
        "rule_changes": args.rule_changes,
        "risk_changes": args.risk_changes,
        "position_changes": args.position_changes,
        "next_action": args.next_action,
        "conclusion": args.conclusion,
        "tags": _split_values(args.tags),
        "notes": args.notes,
    }


def _records(output_root: Path) -> list[dict[str, Any]]:
    records = read_jsonl(records_path(output_root))
    records.sort(key=lambda item: item.get("created_at") or "")
    return records


def _issue_codes(record: dict[str, Any]) -> str:
    codes = [item.get("code") for item in record.get("auto_issues") or [] if item.get("code")]
    codes.extend(record.get("manual_issues") or [])
    return ", ".join(codes) if codes else "无"


def write_iteration_report(output_root: Path) -> Path:
    ensure_dir(reports_dir(output_root))
    records = _records(output_root)
    path = report_path(output_root)
    lines = [
        "# 策略迭代记录报告",
        "",
        "## 总览",
        f"- 记录数量：{len(records)}",
        f"- 生成时间：{now_iso()}",
        "",
        "## 最近记录",
    ]
    if not records:
        lines.append("- 暂无策略迭代记录。")
    for record in records[-20:]:
        metrics = record.get("metrics") or {}
        lines.append(
            "- "
            f"`{record.get('iteration_id')}` "
            f"策略={record.get('strategy_version')} "
            f"来源={record.get('source_type')}:{record.get('source_id')} "
            f"区间={record.get('period_start')}~{record.get('period_end')} "
            f"收益={metrics.get('total_return_pct')}% "
            f"回撤={metrics.get('max_drawdown_pct')}% "
            f"交易={metrics.get('trade_count')} "
            f"结论={record.get('conclusion')} "
            f"问题={_issue_codes(record)}"
        )

    lines.extend(["", "## 记录明细"])
    for record in records[-10:]:
        metrics = record.get("metrics") or {}
        lines.extend(
            [
                "",
                f"### {record.get('iteration_id')}",
                "",
                f"- 策略版本：`{record.get('strategy_version')}`",
                f"- 来源：`{record.get('source_type')}` {record.get('source_id')}",
                f"- 标的：{', '.join(record.get('symbols') or []) or '无'}",
                f"- 区间：{record.get('period_start')} 至 {record.get('period_end')}",
                f"- 总收益率：{metrics.get('total_return_pct')}%",
                f"- 最大回撤：{metrics.get('max_drawdown_pct')}%",
                f"- 交易次数：{metrics.get('trade_count')}",
                f"- 胜率：{metrics.get('win_rate')}%",
                f"- 自动问题：{_issue_codes(record)}",
                f"- 策略假设：{record.get('hypothesis') or '未填写'}",
                f"- 规则改动：{record.get('rule_changes') or '未填写'}",
                f"- 风控改动：{record.get('risk_changes') or '未填写'}",
                f"- 仓位改动：{record.get('position_changes') or '未填写'}",
                f"- 下一步：{record.get('next_action') or '未填写'}",
            ]
        )

    lines.extend(
        [
            "",
            "## 说明",
            "- 本报告用于记录策略实验，不证明策略未来一定有效。",
            "- 不能根据单次回放或短期模拟盘结果直接切换实盘。",
            "- 后续改策略时，应先看错误样本、最大回撤、交易次数和是否跑输基准。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def record_iteration(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_dir)
    record = _build_record(args, output_root)
    append_jsonl(records_path(output_root), record)
    report = write_iteration_report(output_root)
    return {
        "record": record,
        "records_path": str(records_path(output_root)),
        "report_path": str(report),
    }


def record_command(args: argparse.Namespace) -> None:
    result = record_iteration(args)
    record = result["record"]
    print(f"iteration: {record['iteration_id']}")
    print(f"strategy_version: {record['strategy_version']}")
    print(f"source: {record['source_type']} {record.get('source_id')}")
    print(f"auto_issues: {_issue_codes(record)}")
    print(f"records: {result['records_path']}")
    print(f"report: {result['report_path']}")


def list_command(args: argparse.Namespace) -> None:
    output_root = Path(args.output_dir)
    records = _records(output_root)
    limit = args.limit if args.limit and args.limit > 0 else 10
    for record in records[-limit:]:
        metrics = record.get("metrics") or {}
        print(
            f"{record.get('iteration_id')} "
            f"strategy={record.get('strategy_version')} "
            f"source={record.get('source_type')}:{record.get('source_id')} "
            f"return={metrics.get('total_return_pct')} "
            f"drawdown={metrics.get('max_drawdown_pct')} "
            f"trades={metrics.get('trade_count')} "
            f"issues={_issue_codes(record)}"
        )


def report_command(args: argparse.Namespace) -> None:
    path = write_iteration_report(Path(args.output_dir))
    print(f"report: {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record strategy iteration notes from replay or paper trading.")
    parser.add_argument("--output-dir", default="data")
    subparsers = parser.add_subparsers(dest="command", required=True)

    record = subparsers.add_parser("record", help="Create a strategy tuning record.")
    record.add_argument("--source-type", choices=["replay", "paper"], required=True)
    record.add_argument("--source-path")
    record.add_argument("--replay-id")
    record.add_argument("--account")
    record.add_argument("--symbols")
    record.add_argument("--strategy-version")
    record.add_argument("--previous-strategy-version")
    record.add_argument("--hypothesis")
    record.add_argument("--rule-changes")
    record.add_argument("--risk-changes")
    record.add_argument("--position-changes")
    record.add_argument("--issues")
    record.add_argument("--next-action")
    record.add_argument("--conclusion", choices=["KEEP", "CHANGE", "REJECT", "WATCH"], default="WATCH")
    record.add_argument("--tags")
    record.add_argument("--notes")
    record.set_defaults(func=record_command)

    list_parser = subparsers.add_parser("list", help="List recent strategy tuning records.")
    list_parser.add_argument("--limit", type=int, default=10)
    list_parser.set_defaults(func=list_command)

    report = subparsers.add_parser("report", help="Regenerate strategy iteration report.")
    report.set_defaults(func=report_command)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
