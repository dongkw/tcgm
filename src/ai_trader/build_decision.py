"""CLI for the first decision pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .decision_utils import ensure_dir, now_stamp
from .db.sync import mirror_record, mirror_report
from .snapshot_builder import BUY_EVALUATION, HOLDING_REVIEW, build_strategy_snapshot
from .strategy_engine import run_strategy
from .timekeeper import build_time_context


TASK_ALIASES = {
    "buy": BUY_EVALUATION,
    "BUY_EVALUATION": BUY_EVALUATION,
    "sell": HOLDING_REVIEW,
    "holding": HOLDING_REVIEW,
    "HOLDING_REVIEW": HOLDING_REVIEW,
}


def _load_stock_json(code: str, data_dir: Path) -> tuple[dict[str, Any], Path]:
    path = data_dir / f"stock_data_{code}.json"
    if not path.exists():
        raise FileNotFoundError(f"stock json not found: {path}")
    return json.loads(path.read_text(encoding="utf-8")), path


def _write_json(path: Path, data: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_report(path: Path, decision: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    triggers = decision.get("trigger_prices") or {}
    dq = decision.get("data_quality_summary") or {}
    time_context = decision.get("time_context") or {}
    action = decision.get("final_action")
    action_cn = {
        "BUY": "买入",
        "WATCH_SMALL": "观察仓 / 小仓试错",
        "WAIT": "观望",
        "DO_NOT_BUY": "不买入",
        "DATA_BLOCKED": "数据不足，暂停评估",
        "HOLD": "持有",
        "REDUCE_HALF": "减仓一半",
        "REDUCE_TO_WATCH": "减到观察仓",
        "CLEAR": "清仓",
        "NO_SELL_T_PLUS": "触发卖出但 T+1 或可卖数量受限",
        "PRE_EVALUATION": "预评估",
    }.get(action, action)
    task_cn = {
        "BUY_EVALUATION": "买入评估",
        "HOLDING_REVIEW": "持仓卖出/持有评估",
        "WATCHLIST_SCAN": "自选股扫描",
    }.get(decision.get("task_type"), decision.get("task_type"))
    confidence_cn = {
        "HIGH": "高",
        "MEDIUM": "中",
        "LOW": "低",
    }.get(decision.get("confidence"), decision.get("confidence"))
    notes = _report_notes(decision)
    lines = [
        f"# 决策报告 {decision.get('symbol')} {decision.get('name')}",
        "",
        "## 最终结论",
        f"- 评估类型：{task_cn}",
        f"- 结论：{action_cn} (`{action}`)",
        f"- 信心：{confidence_cn}",
        f"- 原因：{_translate_reason(decision.get('action_reason'))}",
        f"- 决策时间：{decision.get('decision_time')}",
        "",
        "## 时间场景",
        f"- 目标交易日：{time_context.get('trade_date')}",
        f"- 数据来源交易日：{time_context.get('source_quote_trade_date')}",
        f"- 当前时段：{_session_cn(time_context.get('session_name'))} (`{time_context.get('session_name')}`)",
        f"- 是否交易日：{time_context.get('is_trading_day')}",
        f"- 数据截止：{time_context.get('effective_data_cutoff')}",
        f"- 时间提示：{time_context.get('time_warnings')}",
        f"- 执行提示：{_execution_time_note(time_context)}",
        "",
        "## 结论说明",
        *[f"- {note}" for note in notes],
        "",
        "## 关键价位",
        f"- 短线减仓参考：{triggers.get('reduce_trigger_price')}（通常对应 20 日线）",
        f"- 清仓/前低参考：{triggers.get('clear_trigger_price')}（通常对应近 20 日低点）",
        f"- 中线趋势参考：{triggers.get('middle_trend_price')}（通常对应 60 日线）",
        f"- 反弹压力/突破参考：{triggers.get('resistance_price')}（通常对应近 20 日高点）",
        "",
        "## 数据质量",
        f"- 评分：{dq.get('score')}",
        f"- 等级：{dq.get('level')}",
        f"- 阻塞缺失字段：{dq.get('blocking_missing_fields')}",
        f"- 提醒缺失字段：{dq.get('warning_missing_fields')}",
        "",
        "## 规则明细",
    ]
    for rule in decision.get("rule_results") or []:
        lines.append(
            f"- {rule.get('rule_id')} `{rule.get('status')}`：{_translate_reason(rule.get('message'))}"
        )
    lines.extend(
        [
            "",
            "## 使用提示",
            "- 本报告是第一轮本地规则引擎输出，不构成投资建议。",
            "- `PRE_EVALUATION` 表示缺少持仓关键信息，不能当作完整卖出结论。",
            "- 买入评估第一轮默认保守，通常不会直接给强 `BUY`。",
            "- 真正执行前仍需结合成本、仓位、可卖数量、买入逻辑和证伪点复核。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _cleanup_old_outputs(output_root: Path, code: str, keep: int) -> None:
    if keep <= 0:
        return
    targets = [
        (output_root / "strategy_snapshots", f"strategy_snapshot_{code}_*.json"),
        (output_root / "decision_results", f"decision_result_{code}_*.json"),
        (output_root / "reports", f"decision_report_{code}_*.md"),
    ]
    for directory, pattern in targets:
        if not directory.exists():
            continue
        files = sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
        for old_file in files[keep:]:
            old_file.unlink()


def _translate_reason(reason: str | None) -> str:
    if not reason:
        return ""
    mapping = {
        "holding cost, position, buy logic, or invalidation point is missing": "缺少成本价、仓位、原买入逻辑或原证伪点，只能做预评估",
        "trend confirmation is incomplete": "趋势确认不完整，暂不满足第一轮买入条件",
        "short-term gain is high; only small watch position is allowed": "短期涨幅较高，最多只能小仓观察",
        "no sell trigger is active": "未触发卖出规则",
        "price is below MA20": "价格跌破 20 日线",
        "price is below MA60": "价格跌破 60 日线",
        "price is below the recent 20-day low": "价格跌破近 20 日低点",
        "blocking data gaps prevent a full decision": "存在阻塞性数据缺口，无法完整评估",
        "required data is usable": "必需数据可用",
        "holding context is incomplete": "持仓上下文不完整",
        "holding context is complete": "持仓上下文完整",
        "price is not below MA20": "价格未跌破 20 日线",
        "price is not below MA60": "价格未跌破 60 日线",
    }
    return mapping.get(reason, reason)


def _session_cn(session: str | None) -> str:
    mapping = {
        "PRE_MARKET": "盘前",
        "CALL_AUCTION": "集合竞价",
        "MORNING_TRADE": "上午盘中",
        "LUNCH_BREAK": "午休",
        "AFTERNOON_TRADE": "下午盘中",
        "POST_MARKET": "盘后",
        "AFTER_HOURS": "非交易时段",
        "NON_TRADING": "非交易日",
    }
    return mapping.get(session, session or "")


def _execution_time_note(time_context: dict[str, Any]) -> str:
    session = time_context.get("session_name")
    if session == "NON_TRADING":
        return "当前不是交易日，本报告只能作为下一交易日前的计划参考，不能作为即时交易指令。"
    if session in {"POST_MARKET", "AFTER_HOURS"}:
        return "当前是盘后或非交易时段，本报告用于复盘和下一交易日计划。"
    if session in {"MORNING_TRADE", "AFTERNOON_TRADE", "CALL_AUCTION"}:
        return "当前属于交易时段，任何动作仍需结合实时价格、可卖数量和人工确认。"
    if session == "PRE_MARKET":
        return "当前属于盘前，本报告用于制定今日计划，盘中触发后再复核。"
    return "当前时间场景未完全识别，交易动作需要人工复核。"


def _report_notes(decision: dict[str, Any]) -> list[str]:
    action = decision.get("final_action")
    if action == "PRE_EVALUATION":
        return [
            "这是缺持仓信息下的预评估，不是完整卖出结论。",
            "需要补充成本价、当前仓位、可卖数量、原买入逻辑和原证伪点。",
        ]
    if action == "HOLD":
        return [
            "当前未触发第一轮卖出规则。",
            "仍需持续跟踪原买入逻辑和证伪点。",
        ]
    if action in {"REDUCE_HALF", "REDUCE_TO_WATCH", "CLEAR"}:
        return [
            "当前触发了减仓或清仓规则。",
            "执行前需要确认 T+1 可卖数量、价格是否仍在触发区间。",
        ]
    if action == "NO_SELL_T_PLUS":
        return [
            "规则触发了卖出倾向，但当前可卖数量不足或受 T+1 限制。",
            "需要等持仓解锁或人工复核账户可卖数量。",
        ]
    if action == "WATCH_SMALL":
        return [
            "当前最多适合作为观察仓或小仓试错。",
            "第一轮策略不会在逻辑、资金、估值和验证不完整时给强买入。",
        ]
    if action == "WAIT":
        return [
            "当前不满足买入触发条件，建议等待更明确的数据、价格或逻辑验证。",
        ]
    if action == "DO_NOT_BUY":
        return [
            "当前命中不买入规则或硬风险，不建议进入。",
        ]
    if action == "DATA_BLOCKED":
        return [
            "关键数据缺失或时间校验失败，暂停完整评估。",
        ]
    return ["请结合规则明细和数据质量复核。"]


def build_decision(code: str, task: str, args: argparse.Namespace) -> dict[str, Any]:
    task_type = TASK_ALIASES[task]
    raw_data, source_path = _load_stock_json(code, Path(args.data_dir))
    time_context = build_time_context(raw_data, task_type, trade_date_override=getattr(args, "trade_date", None))

    user_context = {
        "avg_cost": args.avg_cost,
        "position_pct": args.position_pct,
        "total_quantity": args.total_quantity,
        "available_quantity": args.available_quantity,
        "buy_logic": args.buy_logic,
        "invalidation_point": args.invalidation_point,
        "holding_period": args.holding_period,
        "available_cash": args.available_cash,
        "total_assets": args.total_assets,
        "cash_reserve_pct": args.cash_reserve_pct,
    }

    snapshot = build_strategy_snapshot(
        raw_data,
        time_context,
        task_type,
        user_context=user_context,
        source_file=str(source_path),
    )
    decision = run_strategy(snapshot)

    stamp = now_stamp(__import__("datetime").datetime.fromisoformat(snapshot["decision_time"]))
    output_root = Path(args.output_dir)
    task_slug = task_type.lower()
    snapshot_path = output_root / "strategy_snapshots" / f"strategy_snapshot_{code}_{task_slug}_{stamp}.json"
    decision_path = output_root / "decision_results" / f"decision_result_{code}_{task_slug}_{stamp}.json"
    report_path = output_root / "reports" / f"decision_report_{code}_{task_slug}_{stamp}.md"

    _write_json(snapshot_path, snapshot)
    _write_json(decision_path, decision)
    mirror_record(output_root, "strategy_snapshot", snapshot, source_path=snapshot_path)
    mirror_record(output_root, "decision_result", decision, source_path=decision_path)
    if args.report:
        _write_report(report_path, decision)
        mirror_report(
            output_root,
            report_path,
            report_type="decision",
            symbol=decision.get("symbol"),
            trade_date=(decision.get("trade_date") or (decision.get("time_context") or {}).get("trade_date")),
            strategy_version=decision.get("strategy_version"),
            source_type="decision_result",
            source_id=decision.get("decision_id"),
        )
    _cleanup_old_outputs(output_root, code, args.keep_outputs)

    return {
        "snapshot_path": str(snapshot_path),
        "decision_path": str(decision_path),
        "report_path": str(report_path) if args.report else None,
        "decision": decision,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build first-round strategy snapshot and decision result.")
    parser.add_argument("code", help="A-share stock code, for example 002563")
    parser.add_argument("--task", choices=sorted(TASK_ALIASES), default="sell")
    parser.add_argument("--data-dir", default="data/stock_json")
    parser.add_argument("--output-dir", default="data")
    parser.add_argument("--trade-date", help="Target trading date for a plan, for example 2026-07-06.")
    parser.add_argument("--report", action="store_true", default=True)
    parser.add_argument("--keep-outputs", type=int, default=5, help="Keep latest N generated outputs per stock code per output type.")
    parser.add_argument("--avg-cost", type=float)
    parser.add_argument("--position-pct", type=float)
    parser.add_argument("--total-quantity", type=int)
    parser.add_argument("--available-quantity", type=int)
    parser.add_argument("--buy-logic")
    parser.add_argument("--invalidation-point")
    parser.add_argument("--holding-period", choices=["short", "middle", "long"])
    parser.add_argument("--available-cash", type=float)
    parser.add_argument("--total-assets", type=float)
    parser.add_argument("--cash-reserve-pct", type=float)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = build_decision(args.code, args.task, args)
    decision = result["decision"]
    print(f"snapshot: {result['snapshot_path']}")
    print(f"decision: {result['decision_path']}")
    if result["report_path"]:
        print(f"report: {result['report_path']}")
    print(f"action: {decision['final_action']} confidence={decision['confidence']}")


if __name__ == "__main__":
    main()
