"""Strict contracts for constrained, provider-neutral AI research tasks."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


REQUEST_SCHEMA_VERSION = "ai_research_request.v1"
RESPONSE_SCHEMA_VERSION = "ai_research_response.v1"


class AITask(str, Enum):
    EVIDENCE_EXTRACT = "evidence_extract"
    EVIDENCE_CLASSIFY = "evidence_classify"
    RESEARCH_SUMMARY = "research_summary"


class AIConfidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class AIStance(str, Enum):
    SUPPORT = "SUPPORT"
    NEUTRAL = "NEUTRAL"
    OPPOSE = "OPPOSE"
    UNKNOWN = "UNKNOWN"


class EvidencePolarity(str, Enum):
    SUPPORTING = "SUPPORTING"
    OPPOSING = "OPPOSING"
    NEUTRAL = "NEUTRAL"


class AIResearchError(RuntimeError):
    """Base error that must block AI research consumption."""


class AIContractError(AIResearchError):
    pass


class AIProviderUnavailable(AIResearchError):
    pass


class AIProviderFailure(AIResearchError):
    pass


class AITaskTimeout(AIResearchError):
    pass


@dataclass(frozen=True)
class EvidenceItem:
    evidence_id: str
    path: str
    value: Any
    source_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "path": self.path,
            "value": self.value,
            "source_ids": list(self.source_ids),
        }


@dataclass(frozen=True)
class AIResearchRequest:
    request_id: str
    task: AITask
    task_version: str
    snapshot_id: str
    symbol: str
    evidence: tuple[EvidenceItem, ...]
    schema_version: str = REQUEST_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "task": self.task.value,
            "task_version": self.task_version,
            "snapshot_id": self.snapshot_id,
            "symbol": self.symbol,
            "constraints": {
                "allowed_tasks": [item.value for item in AITask],
                "evidence_ids_required": True,
                "trade_actions_forbidden": True,
                "orders_positions_accounts_forbidden": True,
            },
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True)
class EvidenceReference:
    evidence_id: str
    polarity: EvidencePolarity
    message: str


@dataclass(frozen=True)
class ValidatedResearchResult:
    request_id: str
    task: AITask
    task_version: str
    provider: str
    summary: str
    confidence: AIConfidence
    stance: AIStance
    evidence_refs: tuple[EvidenceReference, ...]
    risks: tuple[Mapping[str, Any], ...]
    schema_version: str = RESPONSE_SCHEMA_VERSION
