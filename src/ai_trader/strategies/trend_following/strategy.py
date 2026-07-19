"""Transparent trend-following evidence; it does not assess company quality."""

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


class TrendFollowingStrategy:
    def __init__(self) -> None:
        self._metadata = load_metadata(Path(__file__).with_name("metadata.json"))

    def metadata(self) -> StrategyMetadata:
        return self._metadata

    def applicable(self, snapshot: AnalysisSnapshot) -> Applicability:
        return Applicability(snapshot.asset_type == "A_STOCK", "适用于有完整日线特征的 A 股")

    def evaluate(self, snapshot: AnalysisSnapshot) -> StrategyEvidence:
        features = FeatureStore(snapshot)
        technical = {name: features.get(f"technical.{name}") for name in (
            "ma20", "ma60", "ma20_slope_up", "ma60_slope_up", "above_ma20",
            "above_ma60", "change_20d_pct", "atr14_pct", "high_20d", "low_20d"
        )}
        rules = (
            _boolean_rule("TF_PRICE_MA20", technical["above_ma20"], "价格站上 MA20", {"ma20": technical["ma20"]}),
            _boolean_rule("TF_PRICE_MA60", technical["above_ma60"], "价格站上 MA60", {"ma60": technical["ma60"]}),
            _boolean_rule("TF_MA20_SLOPE", technical["ma20_slope_up"], "MA20 方向向上", {}),
            _boolean_rule("TF_MA60_SLOPE", technical["ma60_slope_up"], "MA60 方向向上", {}),
            _chase_rule(float(technical["change_20d_pct"])),
            _volatility_rule(float(technical["atr14_pct"])),
        )
        support = tuple(
            {"rule_id": rule.rule_id, "message": rule.message, "evidence": dict(rule.evidence)}
            for rule in rules if rule.status == RuleStatus.PASS
        )
        oppose = tuple(
            {"rule_id": rule.rule_id, "message": rule.message, "evidence": dict(rule.evidence)}
            for rule in rules if rule.status in {RuleStatus.FAIL, RuleStatus.WARN}
        )
        price = snapshot.facts["quote"].get("price")
        hard_override = BuySignal.STRONG_OPPOSE if float(technical["change_20d_pct"]) > 80 else None
        return StrategyEvidence(
            applicable=True,
            applicability_reason="适用于有完整日线特征的 A 股",
            data_status=DataStatus(str(snapshot.data_quality["status"])),
            confidence=Confidence.MEDIUM,
            rule_results=rules,
            supporting_evidence=support,
            opposing_evidence=oppose,
            risks=(
                {"type": "TECHNICAL_ONLY", "message": "趋势成立不能证明基本面和估值合理"},
            ),
            trigger_conditions=(
                {"type": "BREAKOUT", "price": technical["high_20d"], "message": "突破近 20 日高点后复核量价"},
                {"type": "RECLAIM_MA20", "price": technical["ma20"], "message": "未站稳 MA20 时等待重新站回"},
            ),
            invalidation_conditions=(
                {"type": "BREAK_LOW20", "price": technical["low_20d"]},
                {"type": "BREAK_MA60", "price": technical["ma60"]},
            ),
            used_features=self._metadata.required_features,
            hard_override_signal=hard_override,
        )


def _boolean_rule(rule_id: str, value: Any, label: str, evidence: dict[str, Any]) -> RuleResult:
    passed = bool(value)
    return RuleResult(
        rule_id=rule_id,
        status=RuleStatus.PASS if passed else RuleStatus.FAIL,
        severity=RuleSeverity.MAJOR,
        message=label if passed else f"未满足：{label}",
        evidence=evidence,
    )


def _chase_rule(change_20d_pct: float) -> RuleResult:
    if change_20d_pct > 80:
        status, message = RuleStatus.FAIL, "20 日涨幅超过 80%，趋势策略反对追高"
    elif change_20d_pct > 30:
        status, message = RuleStatus.WARN, "20 日涨幅超过 30%，需要等待回踩或平台确认"
    else:
        status, message = RuleStatus.PASS, "20 日涨幅未进入追高风险区"
    return RuleResult("TF_CHASE_RISK", status, RuleSeverity.MAJOR, message, {"change_20d_pct": change_20d_pct})


def _volatility_rule(atr14_pct: float) -> RuleResult:
    if atr14_pct > 8:
        status, message = RuleStatus.FAIL, "ATR14 超过 8%，波动风险过高"
    elif atr14_pct > 5:
        status, message = RuleStatus.WARN, "ATR14 超过 5%，需要降低仓位并扩大止损评估"
    else:
        status, message = RuleStatus.PASS, "ATR14 波动处于当前策略可接受范围"
    return RuleResult("TF_VOLATILITY", status, RuleSeverity.MAJOR, message, {"atr14_pct": atr14_pct})
