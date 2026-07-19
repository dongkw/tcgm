"""CLI for the standalone v0.2 multi-strategy research pipeline."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .analysis_snapshot import build_analysis_snapshot
from .decision_utils import ensure_dir, now_stamp
from .strategies import build_builtin_registry
from .strategy_platform.contracts import MarketPhase, TaskType
from .strategy_platform.pipeline import StrategyPipeline
from .strategy_platform.report import write_strategy_report
from .timekeeper import build_time_context


TASK_ALIASES = {
    "buy": TaskType.BUY,
    "holding": TaskType.HOLDING,
}


def _load_stock_json(code: str, data_dir: Path) -> tuple[dict[str, Any], Path]:
    path = data_dir / f"stock_data_{code}.json"
    if not path.exists():
        raise FileNotFoundError(f"stock json not found: {path}")
    return json.loads(path.read_text(encoding="utf-8")), path


def run_multi_strategy(args: argparse.Namespace) -> dict[str, Any]:
    task_type = TASK_ALIASES[args.task]
    raw_data, source_path = _load_stock_json(args.code, Path(args.data_dir))
    time_context = build_time_context(
        raw_data,
        task_type.value,
        trade_date_override=args.trade_date,
    )
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
        "stock_type": args.stock_type,
        "future_eps": args.future_eps,
        "high_risk": args.high_risk,
        "core_logic": args.core_logic,
        "catalyst": args.catalyst,
        "medium_term_improvement": args.medium_term_improvement,
        "tracking_metric": args.tracking_metric,
        "business_model_stable": args.business_model_stable,
        "profit_quality_5y": args.profit_quality_5y,
        "cashflow_reliable": args.cashflow_reliable,
        "competition_not_worse": args.competition_not_worse,
        "fundamental_invalidation": args.fundamental_invalidation,
        "technical_invalidation": args.technical_invalidation,
        "event_invalidation": args.event_invalidation,
        "logic_still_valid": args.logic_still_valid,
        "thesis_fully_realized": args.thesis_fully_realized,
        "would_rebuy_now": args.would_rebuy_now,
    }
    snapshot = build_analysis_snapshot(
        raw_data,
        time_context,
        task_type,
        market_phase=MarketPhase(args.market_phase),
        user_context=user_context,
        source_file=str(source_path),
    )
    result = StrategyPipeline(build_builtin_registry()).run(snapshot)

    output_root = Path(args.output_dir)
    stamp = now_stamp(datetime.fromisoformat(snapshot.decision_time))
    slug = task_type.value.lower()
    result_path = output_root / "runs" / f"strategy_run_{args.code}_{slug}_{stamp}.json"
    report_path = output_root / "reports" / f"strategy_report_{args.code}_{slug}_{stamp}.md"
    ensure_dir(result_path.parent)
    result_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_strategy_report(report_path, result)
    return {
        "result_path": str(result_path),
        "report_path": str(report_path),
        "result": result,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the v0.2 multi-strategy research pipeline.")
    parser.add_argument("code", help="A-share stock code, for example 000969")
    parser.add_argument("--task", choices=sorted(TASK_ALIASES), default="buy")
    parser.add_argument("--market-phase", choices=[item.value for item in MarketPhase], default="POST_MARKET")
    parser.add_argument("--data-dir", default="data/stock_json")
    parser.add_argument("--output-dir", default="data/strategy_platform")
    parser.add_argument("--trade-date")
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
    parser.add_argument("--stock-type", choices=["STABLE", "GROWTH", "CYCLICAL", "TURNAROUND"])
    parser.add_argument("--future-eps", type=float)
    parser.add_argument("--high-risk", action="store_true", default=None)
    parser.add_argument("--core-logic")
    parser.add_argument("--catalyst")
    parser.add_argument("--medium-term-improvement")
    parser.add_argument("--tracking-metric")
    parser.add_argument("--business-model-stable", action=argparse.BooleanOptionalAction)
    parser.add_argument("--profit-quality-5y", action=argparse.BooleanOptionalAction)
    parser.add_argument("--cashflow-reliable", action=argparse.BooleanOptionalAction)
    parser.add_argument("--competition-not-worse", action=argparse.BooleanOptionalAction)
    parser.add_argument("--fundamental-invalidation")
    parser.add_argument("--technical-invalidation")
    parser.add_argument("--event-invalidation")
    parser.add_argument("--logic-still-valid", action=argparse.BooleanOptionalAction)
    parser.add_argument("--thesis-fully-realized", action=argparse.BooleanOptionalAction)
    parser.add_argument("--would-rebuy-now", action=argparse.BooleanOptionalAction)
    return parser.parse_args()


def main() -> None:
    output = run_multi_strategy(parse_args())
    result = output["result"]
    print(f"result: {output['result_path']}")
    print(f"report: {output['report_path']}")
    print(
        f"conclusion: {result.aggregation.conclusion.value} "
        f"effective={result.aggregation.effective_strategy_count}"
    )


if __name__ == "__main__":
    main()
