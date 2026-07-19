from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.ai_trader.analysis_snapshot import build_analysis_snapshot
from src.ai_trader.strategy_platform.contracts import (
    AnalysisSnapshot,
    Applicability,
    BuySignal,
    CalibrationStatus,
    Confidence,
    DataStatus,
    ImplementationType,
    MarketPhase,
    Maturity,
    RuleResult,
    RuleSeverity,
    RuleStatus,
    StrategyEvidence,
    StrategyMetadata,
    TaskType,
    freeze_json,
)
from src.ai_trader.strategy_platform.scoring import ScoringConfig, parse_scoring_config


FIXTURES = Path(__file__).parent / "fixtures"


def fixed_snapshot() -> AnalysisSnapshot:
    raw = json.loads((FIXTURES / "rising_stock.json").read_text(encoding="utf-8"))
    time_context = {
        "trade_date": "2026-07-06",
        "decision_time": "2026-07-06T16:00:00+08:00",
        "effective_data_cutoff": "2026-07-06T16:00:00+08:00",
        "session_name": "POST_MARKET",
        "time_warnings": [],
    }
    return build_analysis_snapshot(
        raw,
        time_context,
        TaskType.BUY,
        market_phase=MarketPhase.POST_MARKET,
        user_context={
            "stock_type": "GROWTH",
            "holding_period": "middle",
            "future_eps": 1.3,
            "core_logic": "盈利增长由产品升级驱动",
            "catalyst": "下一年度报告验证利润增长",
            "medium_term_improvement": "未来两年扣非利润随产品升级增长",
            "tracking_metric": "季度扣非利润同比增速",
            "fundamental_invalidation": "扣非利润同比转负",
            "technical_invalidation": 10.7,
            "event_invalidation": "核心产品被监管禁售",
        },
        source_file=str(FIXTURES / "rising_stock.json"),
    )


def fixed_holding_snapshot(*, price: float = 9.5) -> AnalysisSnapshot:
    raw = json.loads((FIXTURES / "rising_stock.json").read_text(encoding="utf-8"))
    raw["quote"]["price"] = price
    time_context = {
        "trade_date": "2026-07-06",
        "decision_time": "2026-07-06T16:00:00+08:00",
        "effective_data_cutoff": "2026-07-06T16:00:00+08:00",
        "session_name": "POST_MARKET",
        "time_warnings": [],
    }
    return build_analysis_snapshot(
        raw,
        time_context,
        TaskType.HOLDING,
        market_phase=MarketPhase.POST_MARKET,
        user_context={
            "avg_cost": 11.0,
            "total_quantity": 1000,
            "available_quantity": 0,
            "invalidation_point": 10.0,
            "logic_still_valid": True,
            "thesis_fully_realized": False,
            "would_rebuy_now": True,
        },
    )


def metadata(
    strategy_id: str = "test-trend",
    *,
    required_features: tuple[str, ...] = ("technical.ma20",),
) -> StrategyMetadata:
    return StrategyMetadata(
        strategy_id=strategy_id,
        name="Test trend",
        strategy_family="TREND",
        strategy_version="1.0.0",
        parameter_version="1",
        task_type=TaskType.BUY,
        implementation_type=ImplementationType.RULE_BASED,
        maturity=Maturity.PAPER_ONLY,
        calibration_status=CalibrationStatus.UNCALIBRATED,
        supported_asset_types=("A_STOCK",),
        supported_market_phases=(MarketPhase.POST_MARKET,),
        required_features=required_features,
    )


def scoring_config(meta: StrategyMetadata) -> ScoringConfig:
    return parse_scoring_config(
        {
            "strategy_id": meta.strategy_id,
            "strategy_version": meta.strategy_version,
            "parameter_version": meta.parameter_version,
            "base_score": 50,
            "min_score": 0,
            "max_score": 100,
            "rule_weights": {
                "trend-ok": {"PASS": 30, "FAIL": -40, "UNKNOWN": -10}
            },
            "thresholds": [
                {"min_score": 80, "signal": "STRONG_SUPPORT"},
                {"min_score": 60, "signal": "SUPPORT"},
                {"min_score": 40, "signal": "NEUTRAL"},
                {"min_score": 20, "signal": "OPPOSE"},
                {"min_score": 0, "signal": "STRONG_OPPOSE"}
            ]
        },
        meta,
    )


class PassingStrategy:
    def __init__(self, meta: StrategyMetadata | None = None) -> None:
        self._metadata = meta or metadata()
        self.calls = 0

    def metadata(self) -> StrategyMetadata:
        return self._metadata

    def applicable(self, snapshot: AnalysisSnapshot) -> Applicability:
        return Applicability(snapshot.asset_type == "A_STOCK", "A-share strategy")

    def evaluate(self, snapshot: AnalysisSnapshot) -> StrategyEvidence:
        self.calls += 1
        return StrategyEvidence(
            applicable=True,
            applicability_reason="A-share strategy",
            data_status=DataStatus.GOOD,
            confidence=Confidence.MEDIUM,
            rule_results=(
                RuleResult(
                    rule_id="trend-ok",
                    status=RuleStatus.PASS,
                    severity=RuleSeverity.MAJOR,
                    message="trend is valid",
                    evidence=freeze_json({"ma20": snapshot.features["technical"]["ma20"]}),
                ),
            ),
            supporting_evidence=(freeze_json({"feature": "technical.ma20", "value": 11.8}),),
            used_features=("technical.ma20",),
        )


class FailingStrategy(PassingStrategy):
    def evaluate(self, snapshot: AnalysisSnapshot) -> StrategyEvidence:
        raise RuntimeError("intentional failure")


FIXED_CLOCK = lambda: datetime(2026, 7, 6, 16, 0, tzinfo=timezone.utc)
