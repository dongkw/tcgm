"""Validation at strategy platform trust boundaries."""

from __future__ import annotations

import re
from datetime import date, datetime
from collections.abc import Mapping
from typing import Iterable

from .contracts import (
    AnalysisSnapshot,
    BuySignal,
    HoldingSignal,
    StrategyEvidence,
    StrategyMetadata,
    TaskType,
)


ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")


class ContractValidationError(ValueError):
    """Raised when a strategy platform contract is invalid."""


def _duplicates(values: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def validate_metadata(metadata: StrategyMetadata) -> None:
    if not ID_PATTERN.fullmatch(metadata.strategy_id):
        raise ContractValidationError(
            "strategy_id must be 3-64 lowercase letters, digits, or hyphens"
        )
    if not metadata.name.strip():
        raise ContractValidationError("strategy name is required")
    if not metadata.strategy_family.strip():
        raise ContractValidationError("strategy_family is required")
    if not metadata.strategy_version.strip() or not metadata.parameter_version.strip():
        raise ContractValidationError("strategy and parameter versions are required")
    if not metadata.supported_asset_types:
        raise ContractValidationError("supported_asset_types cannot be empty")
    if not metadata.supported_market_phases:
        raise ContractValidationError("supported_market_phases cannot be empty")
    duplicate_features = _duplicates(metadata.required_features + metadata.optional_features)
    if duplicate_features:
        raise ContractValidationError(
            f"feature dependencies contain duplicates: {sorted(duplicate_features)}"
        )


def validate_snapshot(snapshot: AnalysisSnapshot) -> None:
    required_text = {
        "snapshot_id": snapshot.snapshot_id,
        "schema_version": snapshot.schema_version,
        "symbol": snapshot.symbol,
        "asset_type": snapshot.asset_type,
        "trade_date": snapshot.trade_date,
        "decision_time": snapshot.decision_time,
        "source_cutoff_time": snapshot.source_cutoff_time,
        "feature_set_version": snapshot.feature_set_version,
    }
    missing = [name for name, value in required_text.items() if not str(value).strip()]
    if missing:
        raise ContractValidationError(f"snapshot required fields are missing: {missing}")
    try:
        date.fromisoformat(snapshot.trade_date)
    except ValueError as exc:
        raise ContractValidationError("snapshot trade_date must use YYYY-MM-DD") from exc
    for field_name, value in {
        "decision_time": snapshot.decision_time,
        "source_cutoff_time": snapshot.source_cutoff_time,
    }.items():
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ContractValidationError(f"snapshot {field_name} is not ISO 8601") from exc
        if parsed.utcoffset() is None:
            raise ContractValidationError(f"snapshot {field_name} must include timezone")


def validate_evidence(metadata: StrategyMetadata, evidence: StrategyEvidence) -> None:
    rule_ids = [rule.rule_id for rule in evidence.rule_results]
    duplicate_rules = _duplicates(rule_ids)
    if duplicate_rules:
        raise ContractValidationError(f"duplicate rule ids: {sorted(duplicate_rules)}")

    allowed_signal_type = BuySignal if metadata.task_type == TaskType.BUY else HoldingSignal
    if evidence.hard_override_signal is not None and not isinstance(
        evidence.hard_override_signal, allowed_signal_type
    ):
        raise ContractValidationError(
            f"hard override signal does not match task type {metadata.task_type.value}"
        )

    declared = set(metadata.required_features + metadata.optional_features)
    undeclared = sorted(set(evidence.used_features) - declared)
    if undeclared:
        raise ContractValidationError(
            f"strategy used undeclared features: {undeclared}"
        )
    for field_name in (
        "supporting_evidence",
        "opposing_evidence",
        "risks",
        "trigger_conditions",
        "invalidation_conditions",
    ):
        if any(not isinstance(item, Mapping) for item in getattr(evidence, field_name)):
            raise ContractValidationError(f"{field_name} must contain mapping records")
