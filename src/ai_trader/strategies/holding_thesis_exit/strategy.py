"""Mode two: thesis invalidation and re-buy checks outrank price-only decisions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...feature_store import FeatureStore
from ...strategy_platform.contracts import (
    AnalysisSnapshot,
    Applicability,
    Confidence,
    DataStatus,
    HoldingSignal,
    RuleResult,
    RuleSeverity,
    RuleStatus,
    StrategyEvidence,
    StrategyMetadata,
)
from ..loader import load_metadata


class HoldingThesisExitStrategy:
    def __init__(self) -> None:
        self._metadata = load_metadata(Path(__file__).with_name("metadata.json"))

    def metadata(self) -> StrategyMetadata:
        return self._metadata

    def applicable(self, snapshot: AnalysisSnapshot) -> Applicability:
        quantity = snapshot.position.get("total_quantity")
        return Applicability(quantity is None or float(quantity) > 0, "用于已有持仓的逻辑证伪与兑现检查")

    def evaluate(self, snapshot: AnalysisSnapshot) -> StrategyEvidence:
        features = FeatureStore(snapshot)
        logic_valid = _boolean(features.get("decision_context.logic_still_valid"))
        realized = _boolean(features.get("decision_context.thesis_fully_realized"))
        would_rebuy = _boolean(features.get("decision_context.would_rebuy_now"))
        missing = [name for name, value in {"logic_still_valid": logic_valid, "thesis_fully_realized": realized, "would_rebuy_now": would_rebuy}.items() if value is None]
        rules = (
            _rule("H-LOGIC-VALID", logic_valid, trigger_when=False, trigger_message="原买入逻辑已经失效", safe_message="原买入逻辑仍然成立"),
            _rule("H-THESIS-REALIZED", realized, trigger_when=True, trigger_message="原逻辑已经充分兑现", safe_message="原逻辑尚未充分兑现"),
            _rule("H-WOULD-REBUY", would_rebuy, trigger_when=False, trigger_message="若当前空仓，不愿以当前价格重新买入", safe_message="若当前空仓，仍愿以当前价格重新买入"),
        )
        if missing:
            return StrategyEvidence(
                applicable=True,
                applicability_reason="用于已有持仓的逻辑证伪与兑现检查",
                data_status=DataStatus.BLOCKED,
                confidence=Confidence.LOW,
                rule_results=rules,
                risks=({"type": "MISSING_HOLDING_REVIEW", "fields": missing},),
                used_features=self._metadata.optional_features,
            )
        if logic_valid is False:
            signal = HoldingSignal.EXIT_SUPPORT
        elif realized is True or would_rebuy is False:
            signal = HoldingSignal.REDUCE_SUPPORT
        else:
            signal = HoldingSignal.HOLD_SUPPORT
        return StrategyEvidence(
            applicable=True,
            applicability_reason="用于已有持仓的逻辑证伪与兑现检查",
            data_status=DataStatus(str(snapshot.data_quality["status"])),
            confidence=Confidence.MEDIUM,
            rule_results=rules,
            opposing_evidence=tuple({"rule_id": r.rule_id, "message": r.message, "evidence": dict(r.evidence)} for r in rules if r.status == RuleStatus.PASS),
            used_features=self._metadata.optional_features,
            hard_override_signal=signal,
        )


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "是"}:
        return True
    if text in {"false", "0", "no", "否"}:
        return False
    return None


def _rule(rule_id: str, value: bool | None, *, trigger_when: bool, trigger_message: str, safe_message: str) -> RuleResult:
    if value is None:
        return RuleResult(rule_id, RuleStatus.UNKNOWN, RuleSeverity.BLOCKER, f"缺少判断：{safe_message}", {})
    triggered = value is trigger_when
    return RuleResult(rule_id, RuleStatus.PASS if triggered else RuleStatus.FAIL, RuleSeverity.BLOCKER if rule_id == "H-LOGIC-VALID" else RuleSeverity.MAJOR, trigger_message if triggered else safe_message, {"value": value})
