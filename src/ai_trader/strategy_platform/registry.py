"""Explicit strategy registration without business branches in the runner."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .contracts import MarketPhase, Maturity, Strategy, StrategyMetadata, TaskType
from .scoring import ScoringConfig
from .validation import ContractValidationError, validate_metadata


@dataclass(frozen=True)
class RegisteredStrategy:
    strategy: Strategy
    metadata: StrategyMetadata
    scoring: ScoringConfig


class StrategyRegistry:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], RegisteredStrategy] = {}

    def register(self, strategy: Strategy, scoring: ScoringConfig) -> None:
        metadata = strategy.metadata()
        validate_metadata(metadata)
        if (
            scoring.strategy_id != metadata.strategy_id
            or scoring.strategy_version != metadata.strategy_version
            or scoring.parameter_version != metadata.parameter_version
        ):
            raise ContractValidationError("scoring config identity does not match strategy metadata")

        key = (metadata.strategy_id, metadata.strategy_version)
        if key in self._entries:
            raise ContractValidationError(f"strategy is already registered: {key}")
        if metadata.enabled:
            for entry in self._entries.values():
                if entry.metadata.enabled and entry.metadata.strategy_id == metadata.strategy_id:
                    raise ContractValidationError(
                        f"multiple enabled versions for strategy_id: {metadata.strategy_id}"
                    )
        self._entries[key] = RegisteredStrategy(strategy, metadata, scoring)

    def entries(self) -> tuple[RegisteredStrategy, ...]:
        return tuple(
            self._entries[key]
            for key in sorted(self._entries, key=lambda item: (item[0], item[1]))
        )

    def select(
        self,
        task_type: TaskType,
        market_phase: MarketPhase,
        *,
        allowed_maturities: frozenset[Maturity] | None = None,
    ) -> tuple[RegisteredStrategy, ...]:
        allowed = allowed_maturities or frozenset(
            {Maturity.DRAFT, Maturity.PAPER_ONLY, Maturity.ACTIVE}
        )
        return tuple(
            entry
            for entry in self.entries()
            if entry.metadata.enabled
            and entry.metadata.maturity in allowed
            and entry.metadata.task_type == task_type
            and market_phase in entry.metadata.supported_market_phases
        )

    @property
    def version(self) -> str:
        payload = [
            {
                "metadata": entry.metadata.to_dict(),
                "scoring_config_hash": entry.scoring.config_hash,
            }
            for entry in self.entries()
        ]
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
