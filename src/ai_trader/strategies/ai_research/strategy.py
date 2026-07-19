"""AI research strategy consuming only validated structured research results."""

from __future__ import annotations

from pathlib import Path

from ...ai_interface.contracts import (
    AIResearchError,
    AIStance,
    AITask,
    EvidencePolarity,
)
from ...ai_interface.providers import AIProvider
from ...ai_interface.service import AIResearchService, build_request, evidence_by_id
from ...strategy_platform.contracts import (
    AnalysisSnapshot,
    Applicability,
    Confidence,
    DataStatus,
    RuleResult,
    RuleSeverity,
    RuleStatus,
    StrategyEvidence,
    StrategyMetadata,
)
from ..loader import load_metadata


STANCE_STATUS = {
    AIStance.SUPPORT: RuleStatus.PASS,
    AIStance.NEUTRAL: RuleStatus.WARN,
    AIStance.OPPOSE: RuleStatus.FAIL,
    AIStance.UNKNOWN: RuleStatus.UNKNOWN,
}


class AIResearchStrategy:
    def __init__(self, provider: AIProvider, *, timeout_seconds: float = 15.0) -> None:
        self._service = AIResearchService(provider, timeout_seconds=timeout_seconds)
        self._metadata = load_metadata(Path(__file__).with_name("metadata.json"))

    def metadata(self) -> StrategyMetadata:
        return self._metadata

    def applicable(self, snapshot: AnalysisSnapshot) -> Applicability:
        return Applicability(snapshot.asset_type == "A_STOCK", "AI 仅对标准化 A 股证据做固定研究摘要")

    def evaluate(self, snapshot: AnalysisSnapshot) -> StrategyEvidence:
        try:
            result = self._service.run(AITask.RESEARCH_SUMMARY, snapshot)
        except AIResearchError as exc:
            return StrategyEvidence(
                applicable=True,
                applicability_reason="AI 研究任务被阻断",
                data_status=DataStatus.BLOCKED,
                confidence=Confidence.LOW,
                risks=({
                    "type": "AI_RESEARCH_BLOCKED",
                    "provider": self._service.provider_name,
                    "task": AITask.RESEARCH_SUMMARY.value,
                    "message": str(exc),
                },),
            )

        request = build_request(AITask.RESEARCH_SUMMARY, snapshot)
        catalog = evidence_by_id(request)
        supporting: list[dict] = []
        opposing: list[dict] = []
        neutral: list[dict] = []
        for ref in result.evidence_refs:
            source = catalog[ref.evidence_id]
            record = {
                "evidence_id": ref.evidence_id,
                "path": source.path,
                "value": source.value,
                "source_ids": list(source.source_ids),
                "message": ref.message,
            }
            if ref.polarity == EvidencePolarity.SUPPORTING:
                supporting.append(record)
            elif ref.polarity == EvidencePolarity.OPPOSING:
                opposing.append(record)
            else:
                neutral.append(record)

        risks = list(result.risks)
        if neutral:
            risks.append({"type": "AI_NEUTRAL_EVIDENCE", "evidence": neutral})
        rule = RuleResult(
            "AI_RESEARCH_VIEW",
            STANCE_STATUS[result.stance],
            RuleSeverity.INFO,
            result.summary,
            {
                "stance": result.stance.value,
                "provider": result.provider,
                "task": result.task.value,
                "task_version": result.task_version,
                "request_id": result.request_id,
            },
        )
        used = tuple(dict.fromkeys(
            item["path"].removeprefix("features.")
            for item in supporting + opposing
            if item["path"].startswith("features.")
        ))
        declared = set(self._metadata.required_features + self._metadata.optional_features)
        undeclared = sorted(set(used) - declared)
        if undeclared:
            return StrategyEvidence(
                applicable=True,
                applicability_reason="AI 引用了策略未声明的特征",
                data_status=DataStatus.BLOCKED,
                confidence=Confidence.LOW,
                risks=({
                    "type": "AI_UNDECLARED_FEATURES",
                    "provider": result.provider,
                    "features": undeclared,
                },),
            )
        return StrategyEvidence(
            applicable=True,
            applicability_reason="已消费通过固定 schema 和证据引用校验的 AI 研究摘要",
            data_status=DataStatus(str(snapshot.data_quality["status"])),
            confidence=Confidence(result.confidence.value),
            rule_results=(rule,),
            supporting_evidence=tuple(supporting),
            opposing_evidence=tuple(opposing),
            risks=tuple(risks),
            used_features=used,
        )
