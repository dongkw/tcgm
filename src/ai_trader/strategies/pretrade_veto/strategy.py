"""Gate A from the AI decision engine: deterministic pre-trade vetoes."""

from __future__ import annotations

from pathlib import Path

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


class PretradeVetoStrategy:
    def __init__(self) -> None:
        self._metadata = load_metadata(Path(__file__).with_name("metadata.json"))

    def metadata(self) -> StrategyMetadata:
        return self._metadata

    def applicable(self, snapshot: AnalysisSnapshot) -> Applicability:
        return Applicability(snapshot.asset_type == "A_STOCK", "适用于 A 股买入前置排雷")

    def evaluate(self, snapshot: AnalysisSnapshot) -> StrategyEvidence:
        features = FeatureStore(snapshot)
        a2 = bool(features.get("engine_flags.a2_deducted_profit_negative_2y"))
        a4 = bool(features.get("engine_flags.chase_high_1m_over_80_pct"))
        reductions = tuple(features.get("announcements.减持", ()) or ())
        regulatory = tuple(features.get("announcements.监管", ()) or ())
        rules = (
            _announcement_rule("A-1", reductions, "减持公告需核对是否属于无合理解释的大幅减持"),
            _flag_rule("A-2", a2, "最近两个会计年度扣非净利润均为负"),
            _announcement_rule("A-3", regulatory, "监管公告需核对是否为立案调查或非标审计意见"),
            _flag_rule("A-4", a4, "近 1 个月涨幅超过 80%"),
        )
        veto = a2 or a4
        return StrategyEvidence(
            applicable=True,
            applicability_reason="适用于 A 股买入前置排雷",
            data_status=DataStatus(str(snapshot.data_quality["status"])),
            confidence=Confidence.HIGH if veto else Confidence.MEDIUM,
            rule_results=rules,
            opposing_evidence=tuple(
                {"rule_id": rule.rule_id, "message": rule.message, "evidence": dict(rule.evidence)}
                for rule in rules if rule.status in {RuleStatus.FAIL, RuleStatus.WARN}
            ),
            risks=(
                {"type": "ANNOUNCEMENT_SEMANTIC_REVIEW", "message": "减持与监管公告标题不能代替公告原文复核"},
            ),
            used_features=self._metadata.required_features + self._metadata.optional_features,
            hard_override_signal=BuySignal.STRONG_OPPOSE if veto else BuySignal.NEUTRAL,
        )


def _flag_rule(rule_id: str, triggered: bool, message: str) -> RuleResult:
    return RuleResult(
        rule_id,
        RuleStatus.FAIL if triggered else RuleStatus.PASS,
        RuleSeverity.BLOCKER,
        message if triggered else f"未触发：{message}",
        {"triggered": triggered},
    )


def _announcement_rule(rule_id: str, items: tuple, warning: str) -> RuleResult:
    return RuleResult(
        rule_id,
        RuleStatus.WARN if items else RuleStatus.PASS,
        RuleSeverity.MAJOR,
        warning if items else "当前采集范围内未发现对应公告",
        {"announcement_count": len(items)},
    )
