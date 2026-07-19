"""Execution and validation boundary for constrained AI research."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Mapping

from ..strategy_platform.contracts import AnalysisSnapshot
from .contracts import (
    AIConfidence,
    AIContractError,
    AIProviderFailure,
    AIResearchRequest,
    AIStance,
    AITask,
    AITaskTimeout,
    EvidenceItem,
    EvidencePolarity,
    EvidenceReference,
    RESPONSE_SCHEMA_VERSION,
    ValidatedResearchResult,
)
from .providers import AIProvider, TASK_VERSIONS


_RESPONSE_FIELDS = {
    "schema_version", "request_id", "task", "task_version", "provider",
    "summary", "confidence", "stance", "evidence_refs", "risks",
}
_EVIDENCE_REF_FIELDS = {"evidence_id", "polarity", "message"}
_RISK_FIELDS = {"type", "message", "evidence_ids"}
_FORBIDDEN_FIELDS = {
    "action", "trade_action", "buy", "sell", "order", "orders", "quantity",
    "position", "positions", "target_position", "account", "accounts",
    "signal", "recommendation", "decision",
}
_AI_ALLOWED_FEATURE_PATHS = (
    "technical.ma20",
    "technical.ma60",
    "valuation.pe_ttm",
    "valuation.pb",
)


class AIResearchService:
    def __init__(self, provider: AIProvider, *, timeout_seconds: float = 15.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("AI timeout must be positive")
        self._provider = provider
        self._timeout_seconds = timeout_seconds

    @property
    def provider_name(self) -> str:
        return self._provider.name

    def run(self, task: AITask | str, snapshot: AnalysisSnapshot) -> ValidatedResearchResult:
        parsed_task = _parse_task(task)
        request = build_request(parsed_task, snapshot)
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ai-research")
        future = executor.submit(self._provider.execute, request)
        try:
            response = future.result(timeout=self._timeout_seconds)
        except FutureTimeoutError as exc:
            future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            raise AITaskTimeout(
                f"AI task timed out after {self._timeout_seconds:g}s: {parsed_task.value}"
            ) from exc
        except Exception as exc:
            executor.shutdown(wait=False, cancel_futures=True)
            if isinstance(exc, (AIContractError, AIProviderFailure)):
                raise
            from .contracts import AIResearchError
            if isinstance(exc, AIResearchError):
                raise
            raise AIProviderFailure(f"AI provider {self._provider.name} failed: {exc}") from exc
        else:
            executor.shutdown(wait=True)
        return validate_response(request, response, expected_provider=self._provider.name)


def build_request(task: AITask | str, snapshot: AnalysisSnapshot) -> AIResearchRequest:
    parsed_task = _parse_task(task)
    evidence = _snapshot_evidence(snapshot)
    if not evidence:
        raise AIContractError("AI research requires at least one evidence item")
    request_seed = {
        "task": parsed_task.value,
        "task_version": TASK_VERSIONS[parsed_task],
        "snapshot_id": snapshot.snapshot_id,
        "evidence_ids": [item.evidence_id for item in evidence],
    }
    request_id = "air_" + _digest(request_seed)[:24]
    return AIResearchRequest(
        request_id=request_id,
        task=parsed_task,
        task_version=TASK_VERSIONS[parsed_task],
        snapshot_id=snapshot.snapshot_id,
        symbol=snapshot.symbol,
        evidence=evidence,
    )


def validate_response(
    request: AIResearchRequest,
    response: Mapping[str, Any],
    *,
    expected_provider: str,
) -> ValidatedResearchResult:
    if not isinstance(response, Mapping):
        raise AIContractError("AI response must be an object")
    _reject_forbidden_fields(response)
    unknown = set(response) - _RESPONSE_FIELDS
    if unknown:
        raise AIContractError(f"AI response contains unknown fields: {sorted(unknown)}")
    required = _RESPONSE_FIELDS - {"risks"}
    missing = [field for field in sorted(required) if field not in response]
    if missing:
        raise AIContractError(f"AI response is missing fields: {missing}")
    if response["schema_version"] != RESPONSE_SCHEMA_VERSION:
        raise AIContractError("AI response schema_version does not match")
    if response["request_id"] != request.request_id:
        raise AIContractError("AI response request_id does not match")
    if response["task"] != request.task.value or response["task_version"] != request.task_version:
        raise AIContractError("AI response task or task_version does not match")
    if str(response["provider"]).lower() != str(expected_provider).lower():
        raise AIContractError("AI response provider does not match configured provider")

    summary = str(response["summary"]).strip()
    if not summary:
        raise AIContractError("AI research summary is required")
    try:
        confidence = AIConfidence(str(response["confidence"]))
        stance = AIStance(str(response["stance"]))
    except ValueError as exc:
        raise AIContractError(f"AI response contains an invalid enum: {exc}") from exc

    available = {item.evidence_id for item in request.evidence}
    refs_raw = response["evidence_refs"]
    if not isinstance(refs_raw, (list, tuple)) or not refs_raw:
        raise AIContractError("AI research requires evidence_refs")
    refs: list[EvidenceReference] = []
    for raw in refs_raw:
        if not isinstance(raw, Mapping) or set(raw) != _EVIDENCE_REF_FIELDS:
            raise AIContractError("AI evidence reference does not match the fixed schema")
        evidence_id = str(raw["evidence_id"])
        if evidence_id not in available:
            raise AIContractError(f"AI referenced unknown evidence_id: {evidence_id}")
        try:
            polarity = EvidencePolarity(str(raw["polarity"]))
        except ValueError as exc:
            raise AIContractError(f"invalid evidence polarity: {raw['polarity']}") from exc
        refs.append(EvidenceReference(evidence_id, polarity, str(raw["message"]).strip()))
    if stance == AIStance.SUPPORT and not any(r.polarity == EvidencePolarity.SUPPORTING for r in refs):
        raise AIContractError("AI support stance requires supporting evidence")
    if stance == AIStance.OPPOSE and not any(r.polarity == EvidencePolarity.OPPOSING for r in refs):
        raise AIContractError("AI oppose stance requires opposing evidence")

    risks_raw = response.get("risks") or []
    if not isinstance(risks_raw, (list, tuple)):
        raise AIContractError("AI risks must be an array")
    risks: list[dict[str, Any]] = []
    for raw in risks_raw:
        if not isinstance(raw, Mapping) or set(raw) != _RISK_FIELDS:
            raise AIContractError("AI risk does not match the fixed schema")
        ids = raw["evidence_ids"]
        if not isinstance(ids, (list, tuple)) or any(str(item) not in available for item in ids):
            raise AIContractError("AI risk references unknown evidence_id")
        risks.append({"type": str(raw["type"]), "message": str(raw["message"]), "evidence_ids": list(ids)})

    return ValidatedResearchResult(
        request_id=request.request_id,
        task=request.task,
        task_version=request.task_version,
        provider=str(expected_provider).lower(),
        summary=summary,
        confidence=confidence,
        stance=stance,
        evidence_refs=tuple(refs),
        risks=tuple(risks),
    )


def evidence_by_id(request: AIResearchRequest) -> dict[str, EvidenceItem]:
    return {item.evidence_id: item for item in request.evidence}


def _parse_task(task: AITask | str) -> AITask:
    try:
        return task if isinstance(task, AITask) else AITask(str(task))
    except ValueError as exc:
        raise AIContractError(f"unknown AI task: {task}") from exc


def _snapshot_evidence(snapshot: AnalysisSnapshot) -> tuple[EvidenceItem, ...]:
    source_ids = tuple(
        "src_" + _digest(dict(ref))[:16]
        for ref in snapshot.source_refs
    ) or ("src_" + _digest({"snapshot_id": snapshot.snapshot_id})[:16],)
    records: list[EvidenceItem] = []
    for path, value in _flatten(snapshot.facts, "facts"):
        evidence_id = "ev_" + _digest({"snapshot_id": snapshot.snapshot_id, "path": path, "value": value})[:20]
        records.append(EvidenceItem(evidence_id, path, value, source_ids))
    for feature_path in _AI_ALLOWED_FEATURE_PATHS:
        value = _resolve_mapping_path(snapshot.features, feature_path)
        if value is None:
            continue
        path = f"features.{feature_path}"
        evidence_id = "ev_" + _digest({"snapshot_id": snapshot.snapshot_id, "path": path, "value": value})[:20]
        records.append(EvidenceItem(evidence_id, path, value, source_ids))
    return tuple(records)


def _flatten(value: Any, path: str):
    if isinstance(value, Mapping):
        for key in sorted(value):
            yield from _flatten(value[key], f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _flatten(item, f"{path}.{index}")
    elif value is not None:
        yield path, value


def _resolve_mapping_path(root: Mapping[str, Any], path: str) -> Any:
    current: Any = root
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _reject_forbidden_fields(value: Any, path: str = "response") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_FIELDS:
                raise AIContractError(f"AI response contains forbidden trading field: {path}.{key}")
            _reject_forbidden_fields(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_forbidden_fields(item, f"{path}.{index}")


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
