"""Build immutable v0.2 analysis snapshots from normalized stock payloads."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .decision_utils import get_in, is_missing
from .strategy_platform.contracts import (
    AnalysisSnapshot,
    DataStatus,
    MarketPhase,
    TaskType,
    freeze_json,
)
from .strategy_platform.validation import validate_snapshot


SNAPSHOT_SCHEMA_VERSION = "analysis_snapshot.v0.2"
FEATURE_SET_VERSION = "stock_json_features.v0.3"


def _phase_from_time_context(time_context: Mapping[str, Any]) -> MarketPhase:
    session = str(time_context.get("session_name") or "")
    if session in {"PRE_MARKET", "CALL_AUCTION"}:
        return MarketPhase.PRE_MARKET
    if session in {"MORNING_TRADE", "LUNCH_BREAK", "AFTERNOON_TRADE"}:
        return MarketPhase.INTRADAY
    return MarketPhase.POST_MARKET


def _data_quality(
    raw_data: Mapping[str, Any],
    time_context: Mapping[str, Any],
    user_context: Mapping[str, Any],
) -> dict[str, Any]:
    required = {
        "symbol": "quote.code",
        "name": "quote.name",
        "trade_date": "quote.trade_date",
        "price": "quote.price",
    }
    missing = [label for label, path in required.items() if is_missing(get_in(dict(raw_data), path))]
    warnings = [str(item) for item in time_context.get("time_warnings") or []]
    raw_warnings = [str(item) for item in raw_data.get("data_gaps_for_engine") or []]
    if not is_missing(user_context.get("future_eps")):
        raw_warnings = [item for item in raw_warnings if "未来第3年EPS" not in item]
    warnings.extend(raw_warnings)
    time_blocks = [str(item) for item in time_context.get("blocked_data_reasons") or []]

    if missing or time_blocks:
        status = DataStatus.BLOCKED
    elif warnings:
        status = DataStatus.USABLE
    else:
        status = DataStatus.GOOD

    return {
        "status": status.value,
        "blocking_missing_fields": missing,
        "blocking_time_reasons": time_blocks,
        "warnings": warnings,
    }


def _source_refs(raw_data: Mapping[str, Any], source_file: str | None) -> list[dict[str, Any]]:
    meta = raw_data.get("meta") or {}
    refs = [
        {
            "source": meta.get("source"),
            "observed_at": meta.get("generated_at"),
            "source_file": source_file,
        }
    ]
    return refs


def _content_id(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]
    return f"as_{payload.get('symbol')}_{digest}"


def build_analysis_snapshot(
    raw_data: Mapping[str, Any],
    time_context: Mapping[str, Any],
    task_type: TaskType,
    *,
    market_phase: MarketPhase | None = None,
    user_context: Mapping[str, Any] | None = None,
    source_file: str | None = None,
) -> AnalysisSnapshot:
    """Map current stock payloads into a frozen strategy-platform snapshot."""
    user_context = user_context or {}
    quote = raw_data.get("quote") or {}
    quality = _data_quality(raw_data, time_context, user_context)
    phase = market_phase or _phase_from_time_context(time_context)

    position_keys = (
        "avg_cost",
        "position_pct",
        "total_quantity",
        "available_quantity",
        "buy_logic",
        "invalidation_point",
        "holding_period",
    )
    account_keys = ("available_cash", "total_assets", "cash_reserve_pct")
    position = {key: user_context.get(key) for key in position_keys}
    account = {key: user_context.get(key) for key in account_keys}

    facts = {
        "quote": dict(quote),
        "financial": raw_data.get("financial") or {},
        "events": {
            "announcements": raw_data.get("announcements") or {},
        },
        "ownership": raw_data.get("ownership") or {},
    }
    features = {
        "quote": dict(quote),
        "technical": raw_data.get("technical") or {},
        "valuation": raw_data.get("valuation") or {},
        "engine_flags": raw_data.get("engine_flags") or {},
        "financial": raw_data.get("financial") or {},
        "ownership": raw_data.get("ownership") or {},
        "announcements": raw_data.get("announcements") or {},
        "earnings_forecast": raw_data.get("earnings_forecast") or {},
        "decision_context": dict(user_context),
    }
    trade_date = str(time_context.get("trade_date") or quote.get("trade_date") or "")
    decision_time = str(time_context.get("decision_time") or "")
    cutoff = str(time_context.get("effective_data_cutoff") or decision_time)

    id_payload = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "symbol": quote.get("code"),
        "task_type": task_type.value,
        "market_phase": phase.value,
        "trade_date": trade_date,
        "decision_time": decision_time,
        "feature_set_version": FEATURE_SET_VERSION,
        "facts": facts,
        "features": features,
        "position": position,
        "account": account,
        "data_quality": quality,
        "source_refs": _source_refs(raw_data, source_file),
    }
    snapshot = AnalysisSnapshot(
        snapshot_id=_content_id(id_payload),
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        symbol=str(quote.get("code") or ""),
        name=quote.get("name"),
        asset_type="A_STOCK",
        task_type=task_type,
        market_phase=phase,
        trade_date=trade_date,
        decision_time=decision_time,
        source_cutoff_time=cutoff,
        facts=freeze_json(facts),
        features=freeze_json(features),
        position=freeze_json(position),
        account=freeze_json(account),
        data_quality=freeze_json(quality),
        source_refs=freeze_json(_source_refs(raw_data, source_file)),
        feature_set_version=FEATURE_SET_VERSION,
    )
    validate_snapshot(snapshot)
    return snapshot


def analysis_snapshot_from_dict(data: Mapping[str, Any]) -> AnalysisSnapshot:
    """Restore a validated immutable snapshot from persisted JSON data."""
    snapshot = AnalysisSnapshot(
        snapshot_id=str(data.get("snapshot_id") or ""),
        schema_version=str(data.get("schema_version") or ""),
        symbol=str(data.get("symbol") or ""),
        name=data.get("name"),
        asset_type=str(data.get("asset_type") or ""),
        task_type=TaskType(str(data.get("task_type"))),
        market_phase=MarketPhase(str(data.get("market_phase"))),
        trade_date=str(data.get("trade_date") or ""),
        decision_time=str(data.get("decision_time") or ""),
        source_cutoff_time=str(data.get("source_cutoff_time") or ""),
        facts=freeze_json(data.get("facts") or {}),
        features=freeze_json(data.get("features") or {}),
        position=freeze_json(data.get("position") or {}),
        account=freeze_json(data.get("account") or {}),
        data_quality=freeze_json(data.get("data_quality") or {}),
        source_refs=freeze_json(data.get("source_refs") or []),
        feature_set_version=str(data.get("feature_set_version") or ""),
    )
    validate_snapshot(snapshot)
    return snapshot
