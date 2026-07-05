"""Build point-visible stock JSON and strategy snapshots for replay."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .decision_utils import ensure_dir
from .file_store import write_json
from .historical_data import features_until, previous_bar
from .replay_clock import replay_time_context
from .snapshot_builder import BUY_EVALUATION, HOLDING_REVIEW, build_strategy_snapshot
from .strategy_engine import run_strategy


def build_visible_stock_json(
    symbol: str,
    bars: list[dict[str, Any]],
    target_trade_date: str,
    *,
    name: str | None = None,
    exchange: str | None = None,
    replay_mode: str = "REPLAY_LITE",
) -> tuple[dict[str, Any] | None, str | None]:
    source_index, source_bar = previous_bar(bars, target_trade_date)
    if source_index is None or source_bar is None:
        return None, "NO_PREVIOUS_BAR"

    features = features_until(bars, source_index)
    quote_name = name or source_bar.get("name") or symbol
    quote_exchange = exchange or source_bar.get("exchange") or source_bar.get("market") or _infer_exchange(symbol)

    stock_json = {
        "schema_version": "replay_stock_json.v0.1",
        "replay_mode": replay_mode,
        "quote": {
            "code": symbol,
            "name": quote_name,
            "market": quote_exchange,
            "trade_date": source_bar["trade_date"],
            "price": source_bar.get("close"),
            "open": source_bar.get("open"),
            "high": source_bar.get("high"),
            "low": source_bar.get("low"),
            "close": source_bar.get("close"),
            "pre_close": source_bar.get("pre_close"),
            "volume": source_bar.get("volume"),
            "amount": source_bar.get("amount"),
            "pct_change": source_bar.get("pct_change"),
        },
        "technical": features,
        "valuation": {
            "pe_ttm": source_bar.get("pe_ttm"),
            "pb": source_bar.get("pb"),
            "pe_5y_percentile": source_bar.get("pe_5y_percentile"),
            "pb_5y_percentile": source_bar.get("pb_5y_percentile"),
            "note": "REPLAY_LITE only uses point-visible bar fields unless historical valuation is provided.",
        },
        "financial": {},
        "announcements": {},
        "ownership": {},
        "engine_flags": _engine_flags(features),
        "data_gaps_for_engine": [
            "REPLAY_LITE: financial and announcement fields are disabled unless point-in-time data is provided"
        ],
        "replay": {
            "target_trade_date": target_trade_date,
            "source_quote_trade_date": source_bar["trade_date"],
            "source_bar_index": source_index,
        },
    }
    return stock_json, None


def build_replay_decision(
    *,
    symbol: str,
    stock_json: dict[str, Any],
    target_trade_date: str,
    task_type: str,
    user_context: dict[str, Any] | None,
    output_root: Path,
) -> dict[str, Any]:
    time_context = replay_time_context(
        target_trade_date=target_trade_date,
        source_quote_trade_date=(stock_json.get("quote") or {}).get("trade_date"),
        task_type=task_type,
        session_name="PRE_MARKET",
        exchange=(stock_json.get("quote") or {}).get("market"),
    )
    snapshot = build_strategy_snapshot(stock_json, time_context, task_type, user_context=user_context, source_file=None)
    decision = run_strategy(snapshot)
    _write_replay_artifacts(symbol, task_type, output_root, stock_json, snapshot, decision)
    return {
        "stock_json": stock_json,
        "snapshot": snapshot,
        "decision": decision,
        "stock_json_path": str(_stock_json_path(output_root, symbol)),
        "snapshot_path": str(_snapshot_path(output_root, snapshot)),
        "decision_path": str(_decision_path(output_root, decision)),
    }


def _write_replay_artifacts(
    symbol: str,
    task_type: str,
    output_root: Path,
    stock_json: dict[str, Any],
    snapshot: dict[str, Any],
    decision: dict[str, Any],
) -> None:
    ensure_dir(output_root / "stock_json")
    write_json(_stock_json_path(output_root, symbol), stock_json)
    write_json(_snapshot_path(output_root, snapshot), snapshot)
    write_json(_decision_path(output_root, decision), decision)


def _stock_json_path(output_root: Path, symbol: str) -> Path:
    return output_root / "stock_json" / f"stock_data_{symbol}.json"


def _snapshot_path(output_root: Path, snapshot: dict[str, Any]) -> Path:
    return output_root / "strategy_snapshots" / f"{snapshot['snapshot_id'].replace('ss_', 'strategy_snapshot_', 1)}.json"


def _decision_path(output_root: Path, decision: dict[str, Any]) -> Path:
    return output_root / "decision_results" / f"{decision['decision_id'].replace('dr_', 'decision_result_', 1)}.json"


def _engine_flags(features: dict[str, Any]) -> dict[str, Any]:
    change_20d = features.get("change_20d_pct")
    return {
        "chase_high_1m_over_80_pct": bool(change_20d is not None and change_20d > 80),
        "chase_high_1m_50_to_80_pct": bool(change_20d is not None and 50 <= change_20d <= 80),
        "a2_deducted_profit_negative_2y": False,
    }


def _infer_exchange(symbol: str) -> str:
    return "SH" if symbol.startswith("6") else "SZ"


__all__ = [
    "BUY_EVALUATION",
    "HOLDING_REVIEW",
    "build_replay_decision",
    "build_visible_stock_json",
]
