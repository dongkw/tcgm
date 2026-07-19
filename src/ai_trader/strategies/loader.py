"""Load and validate strategy metadata and scoring configuration."""

from __future__ import annotations

import json
from pathlib import Path

from ..strategy_platform.contracts import (
    AggregationRole,
    CalibrationStatus,
    ImplementationType,
    MarketPhase,
    Maturity,
    Strategy,
    StrategyMetadata,
    TaskType,
)
from ..strategy_platform.scoring import ScoringConfig, load_scoring_config
from ..strategy_platform.validation import validate_metadata


def load_metadata(path: Path) -> StrategyMetadata:
    data = json.loads(path.read_text(encoding="utf-8"))
    metadata = StrategyMetadata(
        strategy_id=str(data["strategy_id"]),
        name=str(data["name"]),
        strategy_family=str(data["strategy_family"]),
        strategy_version=str(data["strategy_version"]),
        parameter_version=str(data["parameter_version"]),
        task_type=TaskType(str(data["task_type"])),
        implementation_type=ImplementationType(str(data["implementation_type"])),
        maturity=Maturity(str(data["maturity"])),
        calibration_status=CalibrationStatus(str(data["calibration_status"])),
        supported_asset_types=tuple(str(item) for item in data["supported_asset_types"]),
        supported_market_phases=tuple(MarketPhase(str(item)) for item in data["supported_market_phases"]),
        aggregation_role=AggregationRole(str(data.get("aggregation_role") or "EVIDENCE")),
        required_features=tuple(str(item) for item in data.get("required_features") or []),
        optional_features=tuple(str(item) for item in data.get("optional_features") or []),
        enabled=bool(data.get("enabled", True)),
    )
    validate_metadata(metadata)
    return metadata


def load_scoring_for_strategy(strategy: Strategy, metadata: StrategyMetadata) -> ScoringConfig:
    module_path = Path(__import__(strategy.__class__.__module__, fromlist=["x"]).__file__).parent
    return load_scoring_config(module_path / "scoring.json", metadata)
