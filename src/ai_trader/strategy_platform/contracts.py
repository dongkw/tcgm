"""Contracts shared by strategies, scoring, aggregation, and persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Protocol


class TextEnum(str, Enum):
    """String enum with stable JSON values."""


class TaskType(TextEnum):
    BUY = "BUY"
    HOLDING = "HOLDING"


class MarketPhase(TextEnum):
    POST_MARKET = "POST_MARKET"
    PRE_MARKET = "PRE_MARKET"
    INTRADAY = "INTRADAY"
    HISTORICAL_REPLAY = "HISTORICAL_REPLAY"
    PAPER_TRADING = "PAPER_TRADING"


class ImplementationType(TextEnum):
    RULE_BASED = "RULE_BASED"
    AI_BASED = "AI_BASED"
    HYBRID = "HYBRID"


class Maturity(TextEnum):
    DRAFT = "DRAFT"
    PAPER_ONLY = "PAPER_ONLY"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class CalibrationStatus(TextEnum):
    UNCALIBRATED = "UNCALIBRATED"
    CALIBRATING = "CALIBRATING"
    CALIBRATED = "CALIBRATED"


class AggregationRole(TextEnum):
    EVIDENCE = "EVIDENCE"
    VETO = "VETO"


class DataStatus(TextEnum):
    GOOD = "GOOD"
    USABLE = "USABLE"
    WEAK = "WEAK"
    BLOCKED = "BLOCKED"


class Confidence(TextEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class RuleStatus(TextEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class RuleSeverity(TextEnum):
    BLOCKER = "BLOCKER"
    MAJOR = "MAJOR"
    MINOR = "MINOR"
    INFO = "INFO"


class BuySignal(TextEnum):
    STRONG_SUPPORT = "STRONG_SUPPORT"
    SUPPORT = "SUPPORT"
    NEUTRAL = "NEUTRAL"
    OPPOSE = "OPPOSE"
    STRONG_OPPOSE = "STRONG_OPPOSE"
    UNKNOWN = "UNKNOWN"


class HoldingSignal(TextEnum):
    HOLD_SUPPORT = "HOLD_SUPPORT"
    REDUCE_SUPPORT = "REDUCE_SUPPORT"
    EXIT_SUPPORT = "EXIT_SUPPORT"
    UNKNOWN = "UNKNOWN"


class RunStatus(TextEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class AggregationConclusion(TextEnum):
    FAVORABLE = "FAVORABLE"
    MIXED = "MIXED"
    UNFAVORABLE = "UNFAVORABLE"
    INSUFFICIENT = "INSUFFICIENT"


class FamilyOpinion(TextEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"
    CONFLICTED = "CONFLICTED"


def freeze_json(value: Any) -> Any:
    """Recursively freeze JSON-like data so strategies cannot mutate snapshots."""
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    return value


def thaw_json(value: Any) -> Any:
    """Convert frozen contract data back to plain JSON-compatible values."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw_json(item) for item in value]
    return value


@dataclass(frozen=True)
class AnalysisSnapshot:
    snapshot_id: str
    schema_version: str
    symbol: str
    name: str | None
    asset_type: str
    task_type: TaskType
    market_phase: MarketPhase
    trade_date: str
    decision_time: str
    source_cutoff_time: str
    facts: Mapping[str, Any]
    features: Mapping[str, Any]
    position: Mapping[str, Any]
    account: Mapping[str, Any]
    data_quality: Mapping[str, Any]
    source_refs: tuple[Mapping[str, Any], ...]
    feature_set_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "facts", freeze_json(self.facts))
        object.__setattr__(self, "features", freeze_json(self.features))
        object.__setattr__(self, "position", freeze_json(self.position))
        object.__setattr__(self, "account", freeze_json(self.account))
        object.__setattr__(self, "data_quality", freeze_json(self.data_quality))
        object.__setattr__(self, "source_refs", freeze_json(self.source_refs))

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "schema_version": self.schema_version,
            "symbol": self.symbol,
            "name": self.name,
            "asset_type": self.asset_type,
            "task_type": self.task_type.value,
            "market_phase": self.market_phase.value,
            "trade_date": self.trade_date,
            "decision_time": self.decision_time,
            "source_cutoff_time": self.source_cutoff_time,
            "facts": thaw_json(self.facts),
            "features": thaw_json(self.features),
            "position": thaw_json(self.position),
            "account": thaw_json(self.account),
            "data_quality": thaw_json(self.data_quality),
            "source_refs": thaw_json(self.source_refs),
            "feature_set_version": self.feature_set_version,
        }


@dataclass(frozen=True)
class StrategyMetadata:
    strategy_id: str
    name: str
    strategy_family: str
    strategy_version: str
    parameter_version: str
    task_type: TaskType
    implementation_type: ImplementationType
    maturity: Maturity
    calibration_status: CalibrationStatus
    supported_asset_types: tuple[str, ...]
    supported_market_phases: tuple[MarketPhase, ...]
    aggregation_role: AggregationRole = AggregationRole.EVIDENCE
    required_features: tuple[str, ...] = ()
    optional_features: tuple[str, ...] = ()
    enabled: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "supported_asset_types", tuple(self.supported_asset_types))
        object.__setattr__(self, "supported_market_phases", tuple(self.supported_market_phases))
        object.__setattr__(self, "required_features", tuple(self.required_features))
        object.__setattr__(self, "optional_features", tuple(self.optional_features))

    def to_dict(self) -> dict[str, Any]:
        return thaw_json(self.__dict__)


@dataclass(frozen=True)
class Applicability:
    applicable: bool
    reason: str


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    status: RuleStatus
    severity: RuleSeverity
    message: str
    evidence: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", freeze_json(self.evidence))

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "status": self.status.value,
            "severity": self.severity.value,
            "message": self.message,
            "evidence": thaw_json(self.evidence),
        }


Signal = BuySignal | HoldingSignal


@dataclass(frozen=True)
class StrategyEvidence:
    applicable: bool
    applicability_reason: str
    data_status: DataStatus
    confidence: Confidence
    rule_results: tuple[RuleResult, ...] = ()
    supporting_evidence: tuple[Mapping[str, Any], ...] = ()
    opposing_evidence: tuple[Mapping[str, Any], ...] = ()
    risks: tuple[Mapping[str, Any], ...] = ()
    trigger_conditions: tuple[Mapping[str, Any], ...] = ()
    invalidation_conditions: tuple[Mapping[str, Any], ...] = ()
    used_features: tuple[str, ...] = ()
    hard_override_signal: Signal | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_results", tuple(self.rule_results))
        for field_name in (
            "supporting_evidence",
            "opposing_evidence",
            "risks",
            "trigger_conditions",
            "invalidation_conditions",
        ):
            object.__setattr__(self, field_name, freeze_json(getattr(self, field_name)))
        object.__setattr__(self, "used_features", tuple(self.used_features))


@dataclass(frozen=True)
class StrategyEvaluation:
    evaluation_id: str
    run_id: str
    snapshot_id: str
    metadata: StrategyMetadata
    applicable: bool
    applicability_reason: str
    data_status: DataStatus
    raw_score: float | None
    calibrated_score: float | None
    signal: Signal
    confidence: Confidence
    rule_results: tuple[RuleResult, ...]
    scoring_details: tuple[Mapping[str, Any], ...]
    supporting_evidence: tuple[Mapping[str, Any], ...]
    opposing_evidence: tuple[Mapping[str, Any], ...]
    risks: tuple[Mapping[str, Any], ...]
    trigger_conditions: tuple[Mapping[str, Any], ...]
    invalidation_conditions: tuple[Mapping[str, Any], ...]
    used_features: tuple[str, ...]
    scoring_config_hash: str | None
    started_at: str
    finished_at: str
    duration_ms: int
    error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_results", tuple(self.rule_results))
        for field_name in (
            "scoring_details",
            "supporting_evidence",
            "opposing_evidence",
            "risks",
            "trigger_conditions",
            "invalidation_conditions",
        ):
            object.__setattr__(self, field_name, freeze_json(getattr(self, field_name)))
        object.__setattr__(self, "used_features", tuple(self.used_features))

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "run_id": self.run_id,
            "snapshot_id": self.snapshot_id,
            "metadata": self.metadata.to_dict(),
            "applicable": self.applicable,
            "applicability_reason": self.applicability_reason,
            "data_status": self.data_status.value,
            "raw_score": self.raw_score,
            "calibrated_score": self.calibrated_score,
            "signal": self.signal.value,
            "confidence": self.confidence.value,
            "rule_results": [item.to_dict() for item in self.rule_results],
            "scoring_details": thaw_json(self.scoring_details),
            "supporting_evidence": thaw_json(self.supporting_evidence),
            "opposing_evidence": thaw_json(self.opposing_evidence),
            "risks": thaw_json(self.risks),
            "trigger_conditions": thaw_json(self.trigger_conditions),
            "invalidation_conditions": thaw_json(self.invalidation_conditions),
            "used_features": list(self.used_features),
            "scoring_config_hash": self.scoring_config_hash,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


@dataclass(frozen=True)
class StrategyRunResult:
    run_id: str
    snapshot_id: str
    status: RunStatus
    registry_version: str
    started_at: str
    finished_at: str
    duration_ms: int
    evaluations: tuple[StrategyEvaluation, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "evaluations", tuple(self.evaluations))

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "snapshot_id": self.snapshot_id,
            "status": self.status.value,
            "registry_version": self.registry_version,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "evaluations": [item.to_dict() for item in self.evaluations],
        }


@dataclass(frozen=True)
class StrategyAggregation:
    aggregation_id: str
    run_id: str
    snapshot_id: str
    task_type: TaskType
    conclusion: AggregationConclusion
    effective_strategy_count: int
    support_count: int
    oppose_count: int
    neutral_count: int
    unknown_count: int
    maturity_summary: Mapping[str, int]
    family_summary: tuple[Mapping[str, Any], ...]
    conflicts: tuple[Mapping[str, Any], ...]
    blocked_strategies: tuple[str, ...]
    failed_strategies: tuple[str, ...]
    aggregator_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "maturity_summary", freeze_json(self.maturity_summary))
        object.__setattr__(self, "family_summary", freeze_json(self.family_summary))
        object.__setattr__(self, "conflicts", freeze_json(self.conflicts))
        object.__setattr__(self, "blocked_strategies", tuple(self.blocked_strategies))
        object.__setattr__(self, "failed_strategies", tuple(self.failed_strategies))

    def to_dict(self) -> dict[str, Any]:
        return {
            "aggregation_id": self.aggregation_id,
            "run_id": self.run_id,
            "snapshot_id": self.snapshot_id,
            "task_type": self.task_type.value,
            "conclusion": self.conclusion.value,
            "effective_strategy_count": self.effective_strategy_count,
            "support_count": self.support_count,
            "oppose_count": self.oppose_count,
            "neutral_count": self.neutral_count,
            "unknown_count": self.unknown_count,
            "maturity_summary": thaw_json(self.maturity_summary),
            "family_summary": thaw_json(self.family_summary),
            "conflicts": thaw_json(self.conflicts),
            "blocked_strategies": list(self.blocked_strategies),
            "failed_strategies": list(self.failed_strategies),
            "aggregator_version": self.aggregator_version,
        }


class Strategy(Protocol):
    def metadata(self) -> StrategyMetadata: ...

    def applicable(self, snapshot: AnalysisSnapshot) -> Applicability: ...

    def evaluate(self, snapshot: AnalysisSnapshot) -> StrategyEvidence: ...
