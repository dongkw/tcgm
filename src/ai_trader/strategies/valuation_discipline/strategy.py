"""Gate D: explicit three-year return math with no invented forecast inputs."""

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


NON_CYCLICAL_TYPES = {"STABLE", "GROWTH", "稳定价值", "成长"}


class ValuationDisciplineStrategy:
    def __init__(self) -> None:
        self._metadata = load_metadata(Path(__file__).with_name("metadata.json"))

    def metadata(self) -> StrategyMetadata:
        return self._metadata

    def applicable(self, snapshot: AnalysisSnapshot) -> Applicability:
        return Applicability(snapshot.asset_type == "A_STOCK", "按文档 D 闸门执行三年回报率估值")

    def evaluate(self, snapshot: AnalysisSnapshot) -> StrategyEvidence:
        features = FeatureStore(snapshot)
        stock_type = str(features.get("decision_context.stock_type") or "").strip().upper()
        future_eps = _number(features.get("decision_context.future_eps"))
        if future_eps is None:
            future_eps = _number(features.get("earnings_forecast.future_eps"))
        missing = []
        if not stock_type:
            missing.append("decision_context.stock_type")
        if future_eps is None:
            missing.append("decision_context.future_eps 或 earnings_forecast.future_eps")
        type_supported = stock_type in NON_CYCLICAL_TYPES
        if stock_type and not type_supported:
            missing.append("周期股/困境反转所需专用估值输入")
        if missing:
            rules = (
                RuleResult("D-TYPE", RuleStatus.UNKNOWN, RuleSeverity.BLOCKER, "估值类型或专用估值输入不足", {"stock_type": stock_type or None}),
                RuleResult("D-RETURN", RuleStatus.UNKNOWN, RuleSeverity.BLOCKER, "缺少未来第 3 年 EPS 等关键假设，不计算预期回报", {"missing": missing}),
            )
            return StrategyEvidence(
                applicable=True,
                applicability_reason="按文档 D 闸门执行三年回报率估值",
                data_status=DataStatus.BLOCKED,
                confidence=Confidence.LOW,
                rule_results=rules,
                risks=({"type": "MISSING_VALUATION_ASSUMPTIONS", "fields": missing},),
                used_features=self._metadata.required_features,
            )

        current_pe = float(features.get("valuation.pe_ttm"))
        target_pe = float(features.get("valuation.pe_5y_median"))
        current_eps = float(features.get("valuation.latest_effective_report.ttm_eps"))
        price = float(features.get("quote.price"))
        dividend = _number(features.get("quote.dividend_yield_pct")) or 0.0
        ratio = (future_eps / current_eps) * (target_pe / current_pe)
        if min(current_pe, target_pe, current_eps, future_eps, ratio) <= 0:
            return StrategyEvidence(
                applicable=True,
                applicability_reason="按文档 D 闸门执行三年回报率估值",
                data_status=DataStatus.BLOCKED,
                confidence=Confidence.LOW,
                rule_results=(RuleResult("D-RETURN", RuleStatus.UNKNOWN, RuleSeverity.BLOCKER, "估值输入必须为正数", {"current_pe": current_pe, "target_pe": target_pe, "current_eps": current_eps, "future_eps": future_eps}),),
                risks=({"type": "INVALID_VALUATION_INPUT", "message": "PE/EPS 输入无法用于三年回报公式"},),
                used_features=self._metadata.required_features + self._metadata.optional_features,
            )

        expected_return = (ratio ** (1 / 3) - 1) * 100 + dividend
        zero_growth_return = ((target_pe / current_pe) ** (1 / 3) - 1) * 100 + dividend
        high_risk = bool(features.get("decision_context.high_risk"))
        pass_threshold = 12.0 if high_risk else 8.0
        reject_threshold = 8.0 if high_risk else 5.0
        if expected_return >= pass_threshold:
            return_status, signal = RuleStatus.PASS, BuySignal.SUPPORT
        elif expected_return >= reject_threshold:
            return_status, signal = RuleStatus.WARN, BuySignal.NEUTRAL
        else:
            return_status, signal = RuleStatus.FAIL, BuySignal.STRONG_OPPOSE
        required_pe = (future_eps / current_eps) * target_pe / ((1 + (pass_threshold - dividend) / 100) ** 3)
        trigger_price = price * required_pe / current_pe
        rules = (
            RuleResult("D-TYPE", RuleStatus.PASS, RuleSeverity.MAJOR, "按非周期股公式估值", {"stock_type": stock_type}),
            RuleResult("D-RETURN", return_status, RuleSeverity.BLOCKER, f"三年预期年化回报 {expected_return:.2f}%，门槛 {pass_threshold:.2f}%", {"expected_return_pct": round(expected_return, 4), "threshold_pct": pass_threshold, "current_eps": current_eps, "future_eps": future_eps, "current_pe": current_pe, "target_pe": target_pe, "dividend_yield_pct": dividend}),
            RuleResult("D-ZERO-GROWTH", RuleStatus.PASS if zero_growth_return >= pass_threshold else RuleStatus.WARN, RuleSeverity.MAJOR, f"零增长压力测试年化回报 {zero_growth_return:.2f}%", {"zero_growth_return_pct": round(zero_growth_return, 4)}),
        )
        return StrategyEvidence(
            applicable=True,
            applicability_reason="按文档 D 闸门执行三年回报率估值",
            data_status=DataStatus(str(snapshot.data_quality["status"])),
            confidence=Confidence.MEDIUM,
            rule_results=rules,
            supporting_evidence=tuple({"rule_id": r.rule_id, "message": r.message, "evidence": dict(r.evidence)} for r in rules if r.status == RuleStatus.PASS),
            opposing_evidence=tuple({"rule_id": r.rule_id, "message": r.message, "evidence": dict(r.evidence)} for r in rules if r.status in {RuleStatus.FAIL, RuleStatus.WARN}),
            trigger_conditions=({"type": "VALUATION_PRICE", "price": round(trigger_price, 3), "message": "达到回报门槛的估算买入价，需保持其他假设不变"},),
            invalidation_conditions=({"type": "EPS_DEVIATION", "threshold_pct": 20, "message": "未来 EPS 偏离假设超过 20% 时必须重估"},),
            used_features=self._metadata.required_features + self._metadata.optional_features,
            hard_override_signal=signal,
        )


def _number(value: Any) -> float | None:
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None
