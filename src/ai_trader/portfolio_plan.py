"""CLI for third-round risk checks and portfolio allocation planning."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .db.sync import mirror_record, mirror_report
from .decision_utils import ensure_dir
from .file_store import append_jsonl, load_json, read_jsonl
from .portfolio import ensure_account_positions, load_accounts, load_positions, now_iso
from .portfolio_construction import build_allocation
from .risk_control import check_decision_risk


def risk_dir(output_root: Path) -> Path:
    return output_root / "risk_control"


def risk_checks_path(output_root: Path) -> Path:
    return risk_dir(output_root) / "risk_checks.jsonl"


def construction_dir(output_root: Path) -> Path:
    return output_root / "portfolio_construction"


def allocation_plans_path(output_root: Path) -> Path:
    return construction_dir(output_root) / "allocation_plans.jsonl"


def order_intents_path(output_root: Path) -> Path:
    return construction_dir(output_root) / "order_intents.jsonl"


def trades_path(output_root: Path) -> Path:
    return output_root / "paper_trading" / "trades.jsonl"


def reports_dir(output_root: Path) -> Path:
    return output_root / "reports"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_snapshot(decision: dict[str, Any], output_root: Path, explicit_dir: Path | None = None) -> tuple[dict[str, Any] | None, Path | None]:
    snapshot_id = decision.get("snapshot_id")
    if not snapshot_id:
        return None, None

    directory = explicit_dir or output_root / "strategy_snapshots"
    expected_name = snapshot_id.replace("ss_", "strategy_snapshot_", 1) + ".json"
    expected_path = directory / expected_name
    if expected_path.exists():
        return _read_json(expected_path), expected_path

    suffix = snapshot_id.replace("ss_", "", 1)
    matches = sorted(directory.glob(f"*{suffix}*.json"))
    if matches:
        return _read_json(matches[0]), matches[0]
    return None, None


def _collect_decisions(decision_dir: Path, decision_paths: list[str] | None, include: set[str] | None) -> list[tuple[dict[str, Any], Path]]:
    paths: list[Path] = []
    if decision_paths:
        paths.extend(Path(path) for path in decision_paths)
    else:
        paths.extend(sorted(decision_dir.glob("decision_result_*.json")))

    loaded: list[tuple[dict[str, Any], Path]] = []
    for path in paths:
        if not path.exists():
            continue
        decision = _read_json(path)
        symbol = decision.get("symbol")
        if include and symbol not in include:
            continue
        if decision.get("task_type") != "BUY_EVALUATION":
            continue
        loaded.append((decision, path))

    latest: dict[tuple[str, str], tuple[dict[str, Any], Path]] = {}
    for decision, path in loaded:
        key = (decision.get("symbol") or "", decision.get("task_type") or "")
        current = latest.get(key)
        if not current or (decision.get("decision_time") or "") > (current[0].get("decision_time") or ""):
            latest[key] = (decision, path)
    return list(latest.values())


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_dir)
    accounts = load_accounts(output_root)
    account = accounts.get(args.account)
    if not account:
        raise ValueError(f"account not found: {args.account}")

    positions = load_positions(output_root)
    account_positions = ensure_account_positions(positions, args.account)
    trade_history = read_jsonl(trades_path(output_root))
    config = load_json(Path(args.risk_config), {}) if args.risk_config else {}
    include = set(args.include.split(",")) if args.include else None
    decisions = _collect_decisions(Path(args.decision_dir), args.decision_path, include)

    candidates: list[dict[str, Any]] = []
    for decision, decision_path in decisions:
        snapshot, snapshot_path = _find_snapshot(decision, output_root, Path(args.snapshot_dir) if args.snapshot_dir else None)
        risk_check = check_decision_risk(
            decision,
            snapshot,
            account,
            account_positions,
            trade_history,
            output_root,
            config,
        )
        risk_check["source_decision_path"] = str(decision_path)
        risk_check["source_snapshot_path"] = str(snapshot_path) if snapshot_path else None
        risk_path = risk_checks_path(output_root)
        append_jsonl(risk_path, risk_check)
        mirror_record(output_root, "risk_check", risk_check, source_path=risk_path)
        candidates.append(
            {
                "decision": decision,
                "decision_path": str(decision_path),
                "snapshot": snapshot,
                "snapshot_path": str(snapshot_path) if snapshot_path else None,
                "risk_check": risk_check,
            }
        )

    allocation = build_allocation(
        candidates,
        account,
        account_positions,
        trade_date=args.trade_date or account.get("last_rollover_trade_date") or "",
        strategy_version=args.strategy_version,
        default_watch_cash=args.default_watch_cash,
        max_candidates=args.max_candidates,
    )
    allocation_plan = allocation["allocation_plan"]
    order_intents = allocation["order_intents"]

    allocation_path = allocation_plans_path(output_root)
    append_jsonl(allocation_path, allocation_plan)
    mirror_record(output_root, "allocation_plan", allocation_plan, source_path=allocation_path)
    intents_path = order_intents_path(output_root)
    for intent in order_intents:
        append_jsonl(intents_path, intent)
        mirror_record(output_root, "order_intent", intent, source_path=intents_path)

    report_path = write_allocation_report(output_root, allocation_plan, order_intents, candidates)
    mirror_report(
        output_root,
        report_path,
        report_type="allocation",
        account_id=allocation_plan.get("account_id"),
        trade_date=allocation_plan.get("trade_date"),
        strategy_version=allocation_plan.get("strategy_version"),
        source_type="allocation_plan",
        source_id=allocation_plan.get("allocation_id"),
    )
    return {
        "allocation_plan": allocation_plan,
        "order_intents": order_intents,
        "report_path": str(report_path),
    }


def write_allocation_report(
    output_root: Path,
    allocation_plan: dict[str, Any],
    order_intents: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> Path:
    ensure_dir(reports_dir(output_root))
    account_id = allocation_plan.get("account_id")
    trade_date = allocation_plan.get("trade_date") or "unknown"
    path = reports_dir(output_root) / f"allocation_report_{account_id}_{trade_date}.md"
    risk_by_decision = {candidate["decision"].get("decision_id"): candidate["risk_check"] for candidate in candidates}

    ready = [intent for intent in order_intents if intent.get("status") == "READY_FOR_CONFIRM"]
    deferred = [intent for intent in order_intents if intent.get("status") == "DEFERRED"]
    rejected = [intent for intent in order_intents if intent.get("status") == "REJECTED"]
    record_only = [intent for intent in order_intents if intent.get("status") == "RECORD_ONLY"]

    lines = [
        f"# 组合计划报告 {account_id} {trade_date}",
        "",
        "## 账户摘要",
        f"- 可用现金：{allocation_plan.get('cash_before')}",
        f"- 现金保留：{allocation_plan.get('cash_reserved')}",
        f"- 今日买入预算：{allocation_plan.get('buy_budget')}",
        f"- 计划买入金额：{allocation_plan.get('planned_buy_amount')}",
        f"- 计划买入数量：{allocation_plan.get('planned_position_count')}",
        f"- 状态：`{allocation_plan.get('status')}`",
        "",
        "## 买入计划",
    ]

    if not ready:
        lines.append("- 当前没有可执行买入计划。")
    for intent in ready:
        lines.append(
            "- "
            f"{intent.get('rank')}. {intent.get('symbol')} {intent.get('name')} "
            f"分数={intent.get('score')} "
            f"金额={intent.get('planned_cash_amount')} "
            f"数量={intent.get('planned_quantity')} "
            f"价格={intent.get('reference_price')}"
        )

    lines.extend(["", "## 延后候选"])
    if not deferred:
        lines.append("- 无。")
    for intent in deferred:
        lines.append(f"- {intent.get('symbol')} {intent.get('name')}：{intent.get('reason')}")

    lines.extend(["", "## 拒绝候选"])
    if not rejected:
        lines.append("- 无。")
    for intent in rejected:
        risk = risk_by_decision.get(intent.get("decision_id")) or {}
        lines.append(f"- {intent.get('symbol')} {intent.get('name')}：{intent.get('reason')} (`{risk.get('risk_status')}`)")

    lines.extend(["", "## 只记录候选"])
    if not record_only:
        lines.append("- 无。")
    for intent in record_only:
        lines.append(f"- {intent.get('symbol')} {intent.get('name')}：{intent.get('reason')}")

    lines.extend(["", "## 评分明细"])
    for intent in order_intents:
        breakdown = intent.get("score_breakdown") or {}
        lines.append(
            "- "
            f"{intent.get('symbol')} `{intent.get('status')}` "
            f"总分={breakdown.get('total_score')} "
            f"信号={breakdown.get('signal_score')} "
            f"信心={breakdown.get('confidence_score')} "
            f"数据={breakdown.get('data_quality_score')} "
            f"组合={breakdown.get('portfolio_fit_score')} "
            f"扣分={breakdown.get('penalty_score')}"
        )

    lines.extend(
        [
            "",
            "## 使用提示",
            "- 本报告是组合买入计划，不是成交记录。",
            "- `READY_FOR_CONFIRM` 仍需要进入模拟盘或人工确认后才会成交。",
            "- 非交易日、未换日、现金不足、价格异常和数据阻塞不会生成可执行计划。",
            "- 最终成交前 `paper_trading` 仍会再次校验现金、时间和数量规则。",
            f"- 生成时间：{now_iso()}",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def build_command(args: argparse.Namespace) -> None:
    result = build_plan(args)
    plan = result["allocation_plan"]
    ready = [intent for intent in result["order_intents"] if intent.get("status") == "READY_FOR_CONFIRM"]
    print(f"allocation: {plan['allocation_id']} status={plan['status']}")
    print(f"candidate_count: {plan['candidate_count']}")
    print(f"ready_intents: {len(ready)}")
    print(f"planned_buy_amount: {plan['planned_buy_amount']}")
    print(f"report: {result['report_path']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build portfolio allocation plan from decision_result files.")
    parser.add_argument("--output-dir", default="data")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Build risk checks and allocation plan.")
    build_parser.add_argument("--account", default="paper_default")
    build_parser.add_argument("--trade-date")
    build_parser.add_argument("--decision-dir", default="data/decision_results")
    build_parser.add_argument("--decision-path", action="append")
    build_parser.add_argument("--snapshot-dir")
    build_parser.add_argument("--include")
    build_parser.add_argument("--strategy-version", default="strategy_v0.1")
    build_parser.add_argument("--max-candidates", type=int)
    build_parser.add_argument("--default-watch-cash", type=float, default=5000.0)
    build_parser.add_argument("--risk-config")
    build_parser.set_defaults(func=build_command)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
