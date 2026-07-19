"""Run independent strategies with dependency checks and error isolation."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Callable

from ..feature_store import FeatureStore
from .contracts import (
    AnalysisSnapshot,
    Applicability,
    Confidence,
    DataStatus,
    Maturity,
    RunStatus,
    StrategyEvaluation,
    StrategyEvidence,
    StrategyRunResult,
    freeze_json,
)
from .registry import RegisteredStrategy, StrategyRegistry
from .scoring import score_evidence, unknown_signal
from .validation import ContractValidationError, validate_evidence, validate_snapshot


Clock = Callable[[], datetime]


def _default_clock() -> datetime:
    return datetime.now().astimezone()


class StrategyRunner:
    def __init__(self, registry: StrategyRegistry, *, clock: Clock | None = None) -> None:
        self._registry = registry
        self._clock = clock or _default_clock

    def run(
        self,
        snapshot: AnalysisSnapshot,
        *,
        allowed_maturities: frozenset[Maturity] | None = None,
    ) -> StrategyRunResult:
        validate_snapshot(snapshot)
        started_clock = self._clock()
        started_perf = time.perf_counter()
        run_id = f"sr_{snapshot.symbol}_{uuid.uuid4().hex}"
        entries = self._registry.select(
            snapshot.task_type,
            snapshot.market_phase,
            allowed_maturities=allowed_maturities,
        )
        evaluations = tuple(self._run_one(run_id, snapshot, entry) for entry in entries)
        error_count = sum(1 for item in evaluations if item.error)
        if not evaluations or error_count == len(evaluations):
            status = RunStatus.FAILED
        elif error_count:
            status = RunStatus.PARTIAL
        else:
            status = RunStatus.COMPLETED
        finished_clock = self._clock()
        return StrategyRunResult(
            run_id=run_id,
            snapshot_id=snapshot.snapshot_id,
            status=status,
            registry_version=self._registry.version,
            started_at=started_clock.isoformat(),
            finished_at=finished_clock.isoformat(),
            duration_ms=max(0, int((time.perf_counter() - started_perf) * 1000)),
            evaluations=evaluations,
        )

    def _run_one(
        self,
        run_id: str,
        snapshot: AnalysisSnapshot,
        entry: RegisteredStrategy,
    ) -> StrategyEvaluation:
        started_clock = self._clock()
        started_perf = time.perf_counter()
        error: str | None = None
        try:
            evidence = self._build_evidence(snapshot, entry)
            validate_evidence(entry.metadata, evidence)
            scoring = score_evidence(entry.metadata, evidence, entry.scoring)
        except Exception as exc:  # Each strategy failure must remain isolated.
            error = f"{type(exc).__name__}: {exc}"
            evidence = StrategyEvidence(
                applicable=False,
                applicability_reason="strategy execution failed",
                data_status=DataStatus.BLOCKED,
                confidence=Confidence.LOW,
            )
            scoring = score_evidence(entry.metadata, evidence, entry.scoring)

        finished_clock = self._clock()
        duration_ms = max(0, int((time.perf_counter() - started_perf) * 1000))
        return StrategyEvaluation(
            evaluation_id=f"se_{entry.metadata.strategy_id}_{uuid.uuid4().hex}",
            run_id=run_id,
            snapshot_id=snapshot.snapshot_id,
            metadata=entry.metadata,
            applicable=evidence.applicable,
            applicability_reason=evidence.applicability_reason,
            data_status=evidence.data_status,
            raw_score=scoring.raw_score,
            calibrated_score=None,
            signal=scoring.signal,
            confidence=evidence.confidence,
            rule_results=evidence.rule_results,
            scoring_details=scoring.details,
            supporting_evidence=evidence.supporting_evidence,
            opposing_evidence=evidence.opposing_evidence,
            risks=evidence.risks,
            trigger_conditions=evidence.trigger_conditions,
            invalidation_conditions=evidence.invalidation_conditions,
            used_features=evidence.used_features,
            scoring_config_hash=scoring.config_hash,
            started_at=started_clock.isoformat(),
            finished_at=finished_clock.isoformat(),
            duration_ms=duration_ms,
            error=error,
        )

    def _build_evidence(
        self,
        snapshot: AnalysisSnapshot,
        entry: RegisteredStrategy,
    ) -> StrategyEvidence:
        snapshot_status = DataStatus(str(snapshot.data_quality.get("status")))
        if snapshot_status == DataStatus.BLOCKED:
            return StrategyEvidence(
                applicable=True,
                applicability_reason="snapshot is applicable but base data is blocked",
                data_status=DataStatus.BLOCKED,
                confidence=Confidence.LOW,
            )

        if snapshot.asset_type not in entry.metadata.supported_asset_types:
            return StrategyEvidence(
                applicable=False,
                applicability_reason=f"unsupported asset type: {snapshot.asset_type}",
                data_status=snapshot_status,
                confidence=Confidence.LOW,
            )

        applicability = entry.strategy.applicable(snapshot)
        if not isinstance(applicability, Applicability):
            raise ContractValidationError("strategy applicable() returned an invalid contract")
        if not applicability.applicable:
            return StrategyEvidence(
                applicable=False,
                applicability_reason=applicability.reason,
                data_status=snapshot_status,
                confidence=Confidence.LOW,
            )

        dependencies = FeatureStore(snapshot).check(entry.metadata.required_features)
        if not dependencies.complete:
            return StrategyEvidence(
                applicable=True,
                applicability_reason=applicability.reason,
                data_status=DataStatus.BLOCKED,
                confidence=Confidence.LOW,
                risks=(
                    freeze_json(
                        {
                            "type": "MISSING_REQUIRED_FEATURES",
                            "features": list(dependencies.missing),
                        }
                    ),
                ),
                used_features=dependencies.available,
            )

        evidence = entry.strategy.evaluate(snapshot)
        if not isinstance(evidence, StrategyEvidence):
            raise ContractValidationError("strategy evaluate() returned an invalid contract")
        return evidence
