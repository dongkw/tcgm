"""Gate E: ownership and crowding risks are filters, never buy reasons."""

from __future__ import annotations

from collections.abc import Mapping
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


class ChipRiskGateStrategy:
    def __init__(self) -> None:
        self._metadata = load_metadata(Path(__file__).with_name("metadata.json"))

    def metadata(self) -> StrategyMetadata:
        return self._metadata

    def applicable(self, snapshot: AnalysisSnapshot) -> Applicability:
        return Applicability(snapshot.asset_type == "A_STOCK", "用于过滤股东分散、融资拥挤与解禁风险")

    def evaluate(self, snapshot: AnalysisSnapshot) -> StrategyEvidence:
        features = FeatureStore(snapshot)
        holders = _mapping(features.get("ownership.shareholder_count.latest"))
        holder_change = _number(holders.get("holder_num_change_pct"))
        holder_surge = holder_change is not None and holder_change > 30
        margin = _mapping(features.get("ownership.margin_trading.latest"))
        margin_net_5d = _number(margin.get("financing_net_buy_5d_yuan"))
        change_20d = _number(features.get("technical.change_20d_pct"))
        margin_stall = margin_net_5d is not None and margin_net_5d > 0 and change_20d is not None and change_20d <= 0
        unlock = _mapping(features.get("ownership.unlock_schedule"))
        unlock_count = int(_number(unlock.get("future_count")) or 0)
        reductions = tuple(features.get("announcements.减持", ()) or ())
        buybacks = tuple(features.get("announcements.回购", ()) or ())
        rules = (
            _count_rule("E-REDUCTION", reductions, "存在减持公告，需核对减持规模与合理解释"),
            RuleResult(
                "E-HOLDER-SURGE",
                RuleStatus.FAIL if holder_surge else RuleStatus.PASS,
                RuleSeverity.BLOCKER,
                "股东户数本期增加超过 30%" if holder_surge else "股东户数未触发本期暴增 30% 的红色闸门",
                {"holder_num_change_pct": holder_change},
            ),
            RuleResult(
                "E-MARGIN-STALL",
                RuleStatus.WARN if margin_stall else RuleStatus.PASS,
                RuleSeverity.MAJOR,
                "近 5 日融资净买入为正但 20 日股价未上涨，存在融资拥挤" if margin_stall else "未识别到融资余额上升且价格滞涨组合",
                {"financing_net_buy_5d_yuan": margin_net_5d, "change_20d_pct": change_20d},
            ),
            RuleResult("E-NORTHBOUND", RuleStatus.UNKNOWN, RuleSeverity.MINOR, "现有北向持股是阶段快照，不能推断持续净流出", {}),
            RuleResult(
                "E-UNLOCK",
                RuleStatus.WARN if unlock_count else RuleStatus.PASS,
                RuleSeverity.MINOR,
                "存在未来解禁记录，买点需结合解禁日期复核" if unlock_count else "当前采集范围内无未来解禁记录",
                {"future_unlock_count": unlock_count},
            ),
            RuleResult(
                "E-BUYBACK",
                RuleStatus.PASS,
                RuleSeverity.INFO,
                "存在回购公告，仅作为信心辅助，不作为买入理由" if buybacks else "当前采集范围内未发现回购公告",
                {"announcement_count": len(buybacks)},
            ),
        )
        return StrategyEvidence(
            applicable=True,
            applicability_reason="用于过滤股东分散、融资拥挤与解禁风险",
            data_status=DataStatus(str(snapshot.data_quality["status"])),
            confidence=Confidence.HIGH if holder_surge else Confidence.MEDIUM,
            rule_results=rules,
            opposing_evidence=tuple(
                {"rule_id": rule.rule_id, "message": rule.message, "evidence": dict(rule.evidence)}
                for rule in rules if rule.status in {RuleStatus.FAIL, RuleStatus.WARN, RuleStatus.UNKNOWN}
            ),
            risks=(
                {"type": "NO_NORTHBOUND_FLOW_INFERENCE", "message": "阶段持股快照不得冒充当日资金流"},
            ),
            used_features=self._metadata.required_features + self._metadata.optional_features,
            hard_override_signal=BuySignal.STRONG_OPPOSE if holder_surge else BuySignal.NEUTRAL,
        )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _count_rule(rule_id: str, items: tuple, message: str) -> RuleResult:
    return RuleResult(rule_id, RuleStatus.WARN if items else RuleStatus.PASS, RuleSeverity.MINOR, message if items else "当前采集范围内未发现对应公告", {"announcement_count": len(items)})
