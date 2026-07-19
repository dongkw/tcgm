"""Compose snapshot execution and transparent aggregation."""

from __future__ import annotations

from dataclasses import dataclass

from .aggregation import aggregate_strategies
from .contracts import AnalysisSnapshot, Maturity, StrategyAggregation, StrategyRunResult
from .registry import StrategyRegistry
from .runner import Clock, StrategyRunner


@dataclass(frozen=True)
class PipelineResult:
    snapshot: AnalysisSnapshot
    run: StrategyRunResult
    aggregation: StrategyAggregation

    def to_dict(self) -> dict:
        return {
            "snapshot": self.snapshot.to_dict(),
            "run": self.run.to_dict(),
            "aggregation": self.aggregation.to_dict(),
        }


class StrategyPipeline:
    def __init__(self, registry: StrategyRegistry, *, clock: Clock | None = None) -> None:
        self._runner = StrategyRunner(registry, clock=clock)

    def run(
        self,
        snapshot: AnalysisSnapshot,
        *,
        allowed_maturities: frozenset[Maturity] | None = None,
    ) -> PipelineResult:
        run = self._runner.run(snapshot, allowed_maturities=allowed_maturities)
        return PipelineResult(snapshot, run, aggregate_strategies(snapshot, run))
