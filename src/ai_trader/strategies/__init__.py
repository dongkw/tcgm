"""Built-in strategy registration."""

from __future__ import annotations

from ..strategy_platform.registry import StrategyRegistry
from ..ai_interface.providers import AIProvider, CodexProvider
from .ai_research.strategy import AIResearchStrategy
from .chip_risk_gate.strategy import ChipRiskGateStrategy
from .fundamental_gate.strategy import FundamentalGateStrategy
from .horizon_fit_gate.strategy import HorizonFitGateStrategy
from .holding_thesis_exit.strategy import HoldingThesisExitStrategy
from .loader import load_metadata, load_scoring_for_strategy
from .pretrade_veto.strategy import PretradeVetoStrategy
from .technical_exit.strategy import TechnicalExitStrategy
from .thesis_completeness.strategy import ThesisCompletenessStrategy
from .trend_following.strategy import TrendFollowingStrategy
from .valuation_discipline.strategy import ValuationDisciplineStrategy


def build_builtin_registry(
    *,
    ai_provider: AIProvider | None = None,
    ai_timeout_seconds: float = 15.0,
) -> StrategyRegistry:
    """Build the explicit v0.2 registry in a stable order."""
    strategies = (
        PretradeVetoStrategy(),
        FundamentalGateStrategy(),
        HorizonFitGateStrategy(),
        ValuationDisciplineStrategy(),
        ChipRiskGateStrategy(),
        ThesisCompletenessStrategy(),
        TrendFollowingStrategy(),
        TechnicalExitStrategy(),
        HoldingThesisExitStrategy(),
        AIResearchStrategy(
            ai_provider or CodexProvider(),
            timeout_seconds=ai_timeout_seconds,
        ),
    )
    registry = StrategyRegistry()
    for strategy in strategies:
        metadata = strategy.metadata()
        registry.register(strategy, load_scoring_for_strategy(strategy, metadata))
    return registry


__all__ = ["build_builtin_registry", "load_metadata"]
