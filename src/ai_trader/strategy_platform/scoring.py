"""Independent, versioned scoring for strategy evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .contracts import (
    BuySignal,
    DataStatus,
    HoldingSignal,
    RuleStatus,
    Signal,
    StrategyEvidence,
    StrategyMetadata,
    TaskType,
    freeze_json,
)
from .validation import ContractValidationError


@dataclass(frozen=True)
class SignalThreshold:
    min_score: float
    signal: Signal


@dataclass(frozen=True)
class ScoringConfig:
    strategy_id: str
    strategy_version: str
    parameter_version: str
    base_score: float
    min_score: float
    max_score: float
    rule_weights: Mapping[str, Mapping[RuleStatus, float]]
    thresholds: tuple[SignalThreshold, ...]
    config_hash: str


@dataclass(frozen=True)
class ScoringResult:
    raw_score: float | None
    signal: Signal
    details: tuple[Mapping[str, Any], ...]
    config_hash: str


def _signal_type(task_type: TaskType):
    return BuySignal if task_type == TaskType.BUY else HoldingSignal


def unknown_signal(task_type: TaskType) -> Signal:
    return _signal_type(task_type)("UNKNOWN")


def load_scoring_config(path: Path, metadata: StrategyMetadata) -> ScoringConfig:
    return parse_scoring_config(json.loads(path.read_text(encoding="utf-8")), metadata)


def parse_scoring_config(data: Mapping[str, Any], metadata: StrategyMetadata) -> ScoringConfig:
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    config_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    identity = {
        "strategy_id": metadata.strategy_id,
        "strategy_version": metadata.strategy_version,
        "parameter_version": metadata.parameter_version,
    }
    for field_name, expected in identity.items():
        if str(data.get(field_name) or "") != expected:
            raise ContractValidationError(
                f"scoring config {field_name} does not match metadata: {expected}"
            )

    min_score = float(data.get("min_score", 0))
    max_score = float(data.get("max_score", 100))
    base_score = float(data.get("base_score", 50))
    if min_score >= max_score:
        raise ContractValidationError("scoring min_score must be below max_score")
    if not min_score <= base_score <= max_score:
        raise ContractValidationError("scoring base_score is outside score range")

    parsed_weights: dict[str, Mapping[RuleStatus, float]] = {}
    for rule_id, status_weights in (data.get("rule_weights") or {}).items():
        if not str(rule_id).strip():
            raise ContractValidationError("scoring rule id cannot be empty")
        parsed: dict[RuleStatus, float] = {}
        for status, delta in status_weights.items():
            parsed[RuleStatus(str(status))] = float(delta)
        parsed_weights[str(rule_id)] = MappingProxyType(parsed)

    signal_type = _signal_type(metadata.task_type)
    thresholds = tuple(
        sorted(
            (
                SignalThreshold(float(item["min_score"]), signal_type(str(item["signal"])))
                for item in data.get("thresholds") or []
            ),
            key=lambda item: item.min_score,
            reverse=True,
        )
    )
    if not thresholds:
        raise ContractValidationError("scoring thresholds cannot be empty")
    threshold_values = [item.min_score for item in thresholds]
    if len(threshold_values) != len(set(threshold_values)):
        raise ContractValidationError("scoring thresholds contain duplicate min_score values")
    if thresholds[-1].min_score > min_score:
        raise ContractValidationError("scoring thresholds do not cover the minimum score")
    if any(item.min_score < min_score or item.min_score > max_score for item in thresholds):
        raise ContractValidationError("scoring threshold is outside score range")

    return ScoringConfig(
        strategy_id=metadata.strategy_id,
        strategy_version=metadata.strategy_version,
        parameter_version=metadata.parameter_version,
        base_score=base_score,
        min_score=min_score,
        max_score=max_score,
        rule_weights=MappingProxyType(parsed_weights),
        thresholds=thresholds,
        config_hash=config_hash,
    )


def score_evidence(
    metadata: StrategyMetadata,
    evidence: StrategyEvidence,
    config: ScoringConfig,
) -> ScoringResult:
    if not evidence.applicable or evidence.data_status == DataStatus.BLOCKED:
        return ScoringResult(None, unknown_signal(metadata.task_type), (), config.config_hash)

    score = config.base_score
    details: list[Mapping[str, Any]] = []
    for rule in evidence.rule_results:
        delta = float((config.rule_weights.get(rule.rule_id) or {}).get(rule.status, 0.0))
        score += delta
        details.append(
            freeze_json(
                {
                    "rule_id": rule.rule_id,
                    "status": rule.status.value,
                    "delta": delta,
                }
            )
        )
    score = round(min(config.max_score, max(config.min_score, score)), 4)

    signal = evidence.hard_override_signal
    if signal is None:
        signal = next(
            (threshold.signal for threshold in config.thresholds if score >= threshold.min_score),
            unknown_signal(metadata.task_type),
        )
    return ScoringResult(score, signal, tuple(details), config.config_hash)
