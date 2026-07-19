"""Gate B: verify that the thesis evidence matches the intended holding horizon."""

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


class HorizonFitGateStrategy:
    def __init__(self) -> None:
        self._metadata = load_metadata(Path(__file__).with_name("metadata.json"))

    def metadata(self) -> StrategyMetadata:
        return self._metadata

    def applicable(self, snapshot: AnalysisSnapshot) -> Applicability:
        return Applicability(snapshot.asset_type == "A_STOCK", "验证短线、中线或长线逻辑与持有周期匹配")

    def evaluate(self, snapshot: AnalysisSnapshot) -> StrategyEvidence:
        features = FeatureStore(snapshot)
        period = str(features.get("decision_context.holding_period") or "").strip().lower()
        rules = [
            RuleResult("B-PERIOD", RuleStatus.PASS if period in {"short", "middle", "long"} else RuleStatus.UNKNOWN, RuleSeverity.BLOCKER, f"持有周期：{period}" if period else "未提供持有周期，不能形成买入结论", {"holding_period": period or None})
        ]
        missing: list[str] = []
        veto = False
        if period == "short":
            catalyst = features.get("decision_context.catalyst")
            status = _text_status(catalyst)
            rules.append(RuleResult("B-SHORT-CATALYST", status, RuleSeverity.BLOCKER, "未来一个月催化剂已提供" if status == RuleStatus.PASS else "短线缺少未来一个月明确催化剂", {"catalyst": catalyst}))
            missing.extend([] if status == RuleStatus.PASS else ["catalyst"])
        elif period == "middle":
            improvement = features.get("decision_context.medium_term_improvement")
            tracking = features.get("decision_context.tracking_metric")
            for rule_id, value, label in (
                ("B-MIDDLE-IMPROVEMENT", improvement, "未来 1-2 年利润或自由现金流改善假设"),
                ("B-MIDDLE-TRACKING", tracking, "量化跟踪指标"),
            ):
                status = _text_status(value)
                rules.append(RuleResult(rule_id, status, RuleSeverity.BLOCKER, f"已提供{label}" if status == RuleStatus.PASS else f"缺少{label}", {"value": value}))
                if status != RuleStatus.PASS:
                    missing.append(rule_id)
        elif period == "long":
            checks = {
                "business_model_stable": _boolean(features.get("decision_context.business_model_stable")),
                "profit_quality_5y": _boolean(features.get("decision_context.profit_quality_5y")),
                "cashflow_reliable": _boolean(features.get("decision_context.cashflow_reliable")),
                "competition_not_worse": _boolean(features.get("decision_context.competition_not_worse")),
            }
            unknown = [key for key, value in checks.items() if value is None]
            veto = any(value is False for value in checks.values())
            status = RuleStatus.UNKNOWN if unknown else RuleStatus.FAIL if veto else RuleStatus.PASS
            rules.append(RuleResult("B-LONG-FOUNDATION", status, RuleSeverity.BLOCKER, "长期复利基础成立" if status == RuleStatus.PASS else "长期复利基础不完整或不成立", {"checks": checks, "missing": unknown}))
            missing.extend(unknown)
        else:
            missing.append("holding_period")

        blocked = bool(missing) and not veto
        signal = BuySignal.STRONG_OPPOSE if veto else None if blocked else BuySignal.NEUTRAL
        return StrategyEvidence(
            applicable=True,
            applicability_reason="验证短线、中线或长线逻辑与持有周期匹配",
            data_status=DataStatus.BLOCKED if blocked else DataStatus(str(snapshot.data_quality["status"])),
            confidence=Confidence.LOW if blocked else Confidence.MEDIUM,
            rule_results=tuple(rules),
            opposing_evidence=tuple({"rule_id": rule.rule_id, "message": rule.message, "evidence": dict(rule.evidence)} for rule in rules if rule.status in {RuleStatus.FAIL, RuleStatus.UNKNOWN}),
            risks=({"type": "MISSING_HORIZON_EVIDENCE", "fields": missing},) if missing else (),
            used_features=self._metadata.optional_features,
            hard_override_signal=signal,
        )


def _text_status(value: Any) -> RuleStatus:
    return RuleStatus.PASS if value is not None and str(value).strip() else RuleStatus.UNKNOWN


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "是"}:
        return True
    if text in {"false", "0", "no", "否"}:
        return False
    return None
