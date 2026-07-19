"""Evaluate technical exit pressure without applying T+1 execution rules."""

from __future__ import annotations

from pathlib import Path

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


class TechnicalExitStrategy:
    def __init__(self) -> None:
        self._metadata = load_metadata(Path(__file__).with_name("metadata.json"))

    def metadata(self) -> StrategyMetadata:
        return self._metadata

    def applicable(self, snapshot: AnalysisSnapshot) -> Applicability:
        quantity = snapshot.position.get("total_quantity")
        return Applicability(quantity is None or float(quantity) > 0, "用于已有持仓的技术退出检查")

    def evaluate(self, snapshot: AnalysisSnapshot) -> StrategyEvidence:
        features = FeatureStore(snapshot)
        price = float(snapshot.facts["quote"]["price"])
        ma20 = float(features.get("technical.ma20"))
        ma60 = float(features.get("technical.ma60"))
        low_20d = float(features.get("technical.low_20d"))
        change_20d = float(features.get("technical.change_20d_pct"))
        invalidation_raw = snapshot.position.get("invalidation_point")
        invalidation, invalidation_warning = _numeric_invalidation(invalidation_raw)

        original_triggered = invalidation is not None and price <= invalidation
        low_triggered = price < low_20d
        ma60_triggered = price < ma60
        ma20_triggered = price < ma20
        surge_triggered = change_20d > 80
        if original_triggered:
            invalidation_status = RuleStatus.PASS
            invalidation_message = "已触发原技术证伪价"
        elif invalidation_warning:
            invalidation_status = RuleStatus.WARN
            invalidation_message = invalidation_warning
        elif invalidation is None:
            invalidation_status = RuleStatus.NOT_APPLICABLE
            invalidation_message = "未提供数值型原技术证伪价"
        else:
            invalidation_status = RuleStatus.FAIL
            invalidation_message = "未触发原技术证伪价"
        rules = (
            RuleResult(
                "TE_ORIGINAL_INVALIDATION",
                invalidation_status,
                RuleSeverity.BLOCKER,
                invalidation_message,
                {"price": price, "invalidation_price": invalidation},
            ),
            _price_rule("TE_BREAK_LOW20", low_triggered, "跌破近 20 日低点", price, low_20d),
            _price_rule("TE_BREAK_MA60", ma60_triggered, "跌破 MA60", price, ma60),
            _price_rule("TE_BREAK_MA20", ma20_triggered, "跌破 MA20", price, ma20),
            RuleResult(
                "TE_SURGE_PROFIT",
                RuleStatus.PASS if surge_triggered else RuleStatus.FAIL,
                RuleSeverity.MAJOR,
                "20 日涨幅超过 80%，支持主动止盈复核" if surge_triggered else "未触发短期暴涨止盈",
                {"change_20d_pct": change_20d},
            ),
        )

        if original_triggered or low_triggered:
            override = HoldingSignal.EXIT_SUPPORT
        elif ma60_triggered or ma20_triggered or surge_triggered:
            override = HoldingSignal.REDUCE_SUPPORT
        else:
            override = HoldingSignal.HOLD_SUPPORT

        support = tuple(
            {"rule_id": rule.rule_id, "message": rule.message, "evidence": dict(rule.evidence)}
            for rule in rules if rule.status == RuleStatus.PASS
        )
        risks = []
        if invalidation_warning:
            risks.append({"type": "UNSTRUCTURED_INVALIDATION", "message": invalidation_warning})
        risks.append({"type": "EXECUTION_NOT_CHECKED", "message": "本策略未处理 T+1 和可卖数量"})
        return StrategyEvidence(
            applicable=True,
            applicability_reason="用于已有持仓的技术退出检查",
            data_status=DataStatus(str(snapshot.data_quality["status"])),
            confidence=Confidence.MEDIUM,
            rule_results=rules,
            supporting_evidence=support,
            risks=tuple(risks),
            trigger_conditions=(
                {"type": "REDUCE", "price": ma20},
                {"type": "EXIT", "price": low_20d},
            ),
            invalidation_conditions=(
                {"type": "ORIGINAL", "price": invalidation},
                {"type": "LOW20", "price": low_20d},
            ),
            used_features=self._metadata.required_features,
            hard_override_signal=override,
        )


def _numeric_invalidation(value):
    if value is None or value == "":
        return None, None
    try:
        return float(value), None
    except (TypeError, ValueError):
        return None, "原证伪点不是可计算价格，只能作为人工文本复核"


def _price_rule(rule_id: str, triggered: bool, label: str, price: float, level: float) -> RuleResult:
    return RuleResult(
        rule_id,
        RuleStatus.PASS if triggered else RuleStatus.FAIL,
        RuleSeverity.MAJOR,
        label if triggered else f"未{label}",
        {"price": price, "level": level},
    )
