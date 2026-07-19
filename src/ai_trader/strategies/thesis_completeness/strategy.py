"""Gate F: validate that a thesis is falsifiable before allowing a buy conclusion."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...feature_store import FeatureStore
from ...strategy_platform.contracts import (
    AnalysisSnapshot,
    Applicability,
    BuySignal,
    Confidence,
    DataStatus,
    RuleResult,
    RuleSeverity,
    RuleStatus,
    StrategyEvidence,
    StrategyMetadata,
)
from ..loader import load_metadata


class ThesisCompletenessStrategy:
    def __init__(self) -> None:
        self._metadata = load_metadata(Path(__file__).with_name("metadata.json"))

    def metadata(self) -> StrategyMetadata:
        return self._metadata

    def applicable(self, snapshot: AnalysisSnapshot) -> Applicability:
        return Applicability(snapshot.asset_type == "A_STOCK", "检查核心逻辑、催化剂和三类证伪信号")

    def evaluate(self, snapshot: AnalysisSnapshot) -> StrategyEvidence:
        features = FeatureStore(snapshot)
        values = {
            "F-CORE": _first(features, "decision_context.core_logic", "decision_context.buy_logic"),
            "F-CATALYST": features.get("decision_context.catalyst"),
            "F-INVALIDATION-FUNDAMENTAL": features.get("decision_context.fundamental_invalidation"),
            "F-INVALIDATION-TECHNICAL": _first(features, "decision_context.technical_invalidation", "decision_context.invalidation_point"),
            "F-INVALIDATION-EVENT": features.get("decision_context.event_invalidation"),
        }
        labels = {
            "F-CORE": "核心逻辑",
            "F-CATALYST": "验证催化剂及最晚时间",
            "F-INVALIDATION-FUNDAMENTAL": "基本面证伪信号",
            "F-INVALIDATION-TECHNICAL": "价格/技术证伪信号",
            "F-INVALIDATION-EVENT": "事件证伪信号",
        }
        missing = [rule_id for rule_id, value in values.items() if not _present(value)]
        rules = tuple(
            RuleResult(
                rule_id,
                RuleStatus.UNKNOWN if rule_id in missing else RuleStatus.PASS,
                RuleSeverity.BLOCKER,
                f"缺少{labels[rule_id]}" if rule_id in missing else f"已提供{labels[rule_id]}",
                {"value": None if rule_id in missing else values[rule_id]},
            )
            for rule_id in values
        )
        blocked = bool(missing)
        return StrategyEvidence(
            applicable=True,
            applicability_reason="检查核心逻辑、催化剂和三类证伪信号",
            data_status=DataStatus.BLOCKED if blocked else DataStatus(str(snapshot.data_quality["status"])),
            confidence=Confidence.LOW if blocked else Confidence.MEDIUM,
            rule_results=rules,
            supporting_evidence=tuple({"rule_id": r.rule_id, "message": r.message} for r in rules if r.status == RuleStatus.PASS),
            opposing_evidence=tuple({"rule_id": r.rule_id, "message": r.message} for r in rules if r.status == RuleStatus.UNKNOWN),
            risks=({"type": "MISSING_THESIS_FIELDS", "rules": missing},) if missing else (),
            used_features=self._metadata.optional_features,
            hard_override_signal=None if blocked else BuySignal.NEUTRAL,
        )


def _first(features: FeatureStore, *paths: str) -> Any:
    return next((value for path in paths if _present(value := features.get(path))), None)


def _present(value: Any) -> bool:
    return value is not None and str(value).strip() not in {"", "未知", "未记录"}
