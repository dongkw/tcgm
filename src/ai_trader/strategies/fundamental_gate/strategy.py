"""Gate C from the AI decision engine using auditable annual financial facts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

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


class FundamentalGateStrategy:
    def __init__(self) -> None:
        self._metadata = load_metadata(Path(__file__).with_name("metadata.json"))

    def metadata(self) -> StrategyMetadata:
        return self._metadata

    def applicable(self, snapshot: AnalysisSnapshot) -> Applicability:
        return Applicability(snapshot.asset_type == "A_STOCK", "使用最近三年年报执行基本面硬否决")

    def evaluate(self, snapshot: AnalysisSnapshot) -> StrategyEvidence:
        features = FeatureStore(snapshot)
        annual = tuple(features.get("financial.annual_last_3", ()) or ())
        a2 = bool(features.get("engine_flags.a2_deducted_profit_negative_2y"))
        c3 = bool(features.get("engine_flags.c3_ocf_low_3y"))
        c1 = _deducted_quality_failed(annual[:2])
        latest = annual[0]
        latest_profit = _number(latest.get("parent_net_profit_yuan"))
        stable_growth = _stable_growth_evidence(annual)
        c2_warning = latest_profit is not None and latest_profit < 50_000_000 and not stable_growth
        latest_ocf_ratio = _number(latest.get("ocf_to_net_profit"))
        regulatory = tuple(features.get("announcements.监管", ()) or ())
        rules = (
            _binary("A-2", a2, "最近两个会计年度扣非净利润均为负", RuleSeverity.BLOCKER),
            _binary("C-1", c1, "连续两年扣非净利润低于归母净利润的 50%", RuleSeverity.BLOCKER),
            RuleResult(
                "C-2",
                RuleStatus.WARN if c2_warning else RuleStatus.PASS,
                RuleSeverity.MAJOR,
                "年归母净利润低于 5000 万且未观察到稳定增长证据" if c2_warning else "未触发小利润且缺乏增长证据组合",
                {"latest_parent_net_profit_yuan": latest_profit, "stable_growth_evidence": stable_growth},
            ),
            _binary("C-3", c3, "经营现金流/净利润连续三年低于 0.5", RuleSeverity.BLOCKER),
            _unknown("C-4", "缺少应收账款和存货连续变化数据，不能机械判定"),
            _unknown("C-5", "缺少商誉/净资产及并购标的风险数据，不能机械判定"),
            RuleResult(
                "C-6",
                RuleStatus.WARN if regulatory else RuleStatus.PASS,
                RuleSeverity.MAJOR,
                "存在监管公告，必须阅读原文核对重大负面" if regulatory else "当前采集范围内未发现监管公告",
                {"announcement_count": len(regulatory)},
            ),
            RuleResult(
                "C-CASHFLOW-LATEST",
                RuleStatus.WARN if latest_ocf_ratio is not None and latest_ocf_ratio < 0.5 else RuleStatus.PASS,
                RuleSeverity.MINOR,
                "最近一年经营现金流/净利润低于 0.5，信心需下调" if latest_ocf_ratio is not None and latest_ocf_ratio < 0.5 else "最近一年现金流质量未触发降级",
                {"latest_ocf_to_net_profit": latest_ocf_ratio},
            ),
        )
        veto = a2 or c1 or c3
        return StrategyEvidence(
            applicable=True,
            applicability_reason="使用最近三年年报执行基本面硬否决",
            data_status=DataStatus(str(snapshot.data_quality["status"])),
            confidence=Confidence.HIGH if veto else Confidence.MEDIUM,
            rule_results=rules,
            opposing_evidence=tuple(
                {"rule_id": rule.rule_id, "message": rule.message, "evidence": dict(rule.evidence)}
                for rule in rules if rule.status in {RuleStatus.FAIL, RuleStatus.WARN, RuleStatus.UNKNOWN}
            ),
            risks=(
                {"type": "MISSING_BALANCE_SHEET_TRENDS", "message": "C-4、C-5 尚无足够结构化数据"},
            ),
            used_features=self._metadata.required_features + self._metadata.optional_features,
            hard_override_signal=BuySignal.STRONG_OPPOSE if veto else BuySignal.NEUTRAL,
        )


def _number(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _deducted_quality_failed(reports: tuple[Mapping[str, Any], ...]) -> bool:
    if len(reports) < 2:
        return False
    failed = []
    for report in reports:
        parent = _number(report.get("parent_net_profit_yuan"))
        deducted = _number(report.get("deducted_net_profit_yuan"))
        failed.append(parent is not None and parent > 0 and deducted is not None and deducted < parent * 0.5)
    return all(failed)


def _stable_growth_evidence(reports: tuple[Mapping[str, Any], ...]) -> bool:
    if len(reports) < 2:
        return False
    newest, previous = reports[0], reports[1]
    return all(
        _number(newest.get(field)) is not None
        and _number(previous.get(field)) is not None
        and _number(newest.get(field)) > _number(previous.get(field))
        for field in ("revenue_yuan", "parent_net_profit_yuan")
    )


def _binary(rule_id: str, triggered: bool, message: str, severity: RuleSeverity) -> RuleResult:
    return RuleResult(rule_id, RuleStatus.FAIL if triggered else RuleStatus.PASS, severity, message if triggered else f"未触发：{message}", {"triggered": triggered})


def _unknown(rule_id: str, message: str) -> RuleResult:
    return RuleResult(rule_id, RuleStatus.UNKNOWN, RuleSeverity.MAJOR, message, {})
