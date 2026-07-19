"""Replaceable providers for constrained AI research."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .contracts import (
    AIProviderFailure,
    AIProviderUnavailable,
    AIResearchRequest,
    AITask,
    RESPONSE_SCHEMA_VERSION,
)


class AIProvider(Protocol):
    @property
    def name(self) -> str: ...

    def execute(self, request: AIResearchRequest) -> Mapping[str, Any]: ...


class CodexProvider:
    """Disabled Codex boundary until an explicit API/CLI executor is configured.

    The local application cannot call the current Codex conversation. A future
    integration may inject an authenticated executor without changing strategies.
    """

    name = "codex"

    def __init__(
        self,
        executor: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    ) -> None:
        self._executor = executor

    def execute(self, request: AIResearchRequest) -> Mapping[str, Any]:
        if self._executor is None:
            raise AIProviderUnavailable(
                "Codex provider is disabled: no authenticated API/CLI executor is configured; "
                "the program cannot call the current Codex conversation"
            )
        return self._executor(request.to_dict())


class CodexCliProvider:
    """Run a fixed research task through an explicitly installed Codex CLI."""

    name = "codex"

    def __init__(
        self,
        *,
        executable: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 120.0,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Codex CLI timeout must be positive")
        self._executable = executable
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._runner = runner

    def execute(self, request: AIResearchRequest) -> Mapping[str, Any]:
        command_prefix = _codex_command_prefix(self._executable)
        if not command_prefix:
            raise AIProviderUnavailable("Codex CLI executable was not found")
        request_json = json.dumps(request.to_dict(), ensure_ascii=False, separators=(",", ":"))
        if len(request_json.encode("utf-8")) > 256_000:
            raise AIProviderFailure("Codex research request exceeds the 256 KB safety limit")

        with tempfile.TemporaryDirectory(prefix="ai-trader-codex-") as temp_dir:
            root = Path(temp_dir)
            schema_path = root / "response.schema.json"
            output_path = root / "response.json"
            schema_path.write_text(
                json.dumps(_response_schema(request), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            args = [
                *command_prefix, "exec", "--ephemeral", "--ignore-rules", "--ignore-user-config",
                "--sandbox", "read-only", "--skip-git-repo-check",
                "--config", 'model_reasoning_effort="low"',
                "--output-schema", str(schema_path),
                "--output-last-message", str(output_path),
            ]
            if self._model:
                args.extend(["--model", self._model])
            args.append("-")
            prompt = (
                "你是受约束的证券研究结构化任务执行器。禁止调用任何工具、网络、文件或外部知识；"
                "禁止输出买卖动作、订单、仓位、账户修改和策略评分。只允许依据 REQUEST 中列出的证据，"
                "返回符合输出 Schema 的单个 JSON 对象。summary 必须明确区分事实与不确定性，所有判断必须引用 evidence_id。\n"
                f"REQUEST={request_json}"
            )
            try:
                completed = self._runner(
                    args,
                    input=prompt,
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    cwd=str(root),
                    timeout=self._timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise AIProviderFailure(
                    f"Codex CLI timed out after {self._timeout_seconds:g}s"
                ) from exc
            except OSError as exc:
                raise AIProviderFailure(f"Codex CLI could not start: {exc}") from exc
            if completed.returncode != 0:
                error = (completed.stderr or completed.stdout or "unknown error").strip()
                raise AIProviderFailure(f"Codex CLI exited with {completed.returncode}: {error[-3000:]}")
            if not output_path.exists():
                raise AIProviderFailure("Codex CLI did not write a structured response")
            try:
                response = json.loads(output_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise AIProviderFailure(f"Codex CLI response is not valid JSON: {exc}") from exc
        if not isinstance(response, Mapping):
            raise AIProviderFailure("Codex CLI response must be a JSON object")
        return response


class ManualProvider:
    """Explicitly supplied results for manual review and deterministic tests."""

    name = "manual"

    def __init__(
        self,
        response: Mapping[str, Any] | Callable[[AIResearchRequest], Mapping[str, Any]],
    ) -> None:
        self._response = response

    def execute(self, request: AIResearchRequest) -> Mapping[str, Any]:
        if callable(self._response):
            return dict(self._response(request))
        return dict(self._response)


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, AIProvider] = {}

    def register(self, provider: AIProvider, *, replace: bool = False) -> None:
        name = str(provider.name).strip().lower()
        if not name:
            raise ValueError("AI provider name is required")
        if name in self._providers and not replace:
            raise ValueError(f"AI provider already registered: {name}")
        self._providers[name] = provider

    def get(self, name: str) -> AIProvider:
        key = str(name).strip().lower()
        try:
            return self._providers[key]
        except KeyError as exc:
            raise AIProviderUnavailable(f"AI provider is not registered: {key}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))


def default_provider_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(CodexProvider())
    return registry


def provider_from_name(name: str, *, model: str | None = None) -> AIProvider:
    normalized = str(name or "").strip().lower()
    if normalized in {"", "disabled", "codex-disabled"}:
        return CodexProvider()
    if normalized in {"codex", "codex-cli"}:
        return CodexCliProvider(model=model)
    raise AIProviderUnavailable(f"AI provider is not configured: {normalized}")


TASK_VERSIONS = {
    AITask.EVIDENCE_EXTRACT: "1.0",
    AITask.EVIDENCE_CLASSIFY: "1.0",
    AITask.RESEARCH_SUMMARY: "1.0",
}


def _response_schema(request: AIResearchRequest) -> dict[str, Any]:
    evidence_ids = [item.evidence_id for item in request.evidence]
    evidence_id_schema = {"type": "string", "enum": evidence_ids}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version", "request_id", "task", "task_version", "provider",
            "summary", "confidence", "stance", "evidence_refs", "risks",
        ],
        "properties": {
            "schema_version": {"type": "string", "const": RESPONSE_SCHEMA_VERSION},
            "request_id": {"type": "string", "const": request.request_id},
            "task": {"type": "string", "const": request.task.value},
            "task_version": {"type": "string", "const": request.task_version},
            "provider": {"type": "string", "const": "codex"},
            "summary": {"type": "string", "minLength": 1, "maxLength": 2000},
            "confidence": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
            "stance": {"type": "string", "enum": ["SUPPORT", "NEUTRAL", "OPPOSE", "UNKNOWN"]},
            "evidence_refs": {
                "type": "array", "minItems": 1, "maxItems": 20,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["evidence_id", "polarity", "message"],
                    "properties": {
                        "evidence_id": evidence_id_schema,
                        "polarity": {"type": "string", "enum": ["SUPPORTING", "OPPOSING", "NEUTRAL"]},
                        "message": {"type": "string", "maxLength": 500},
                    },
                },
            },
            "risks": {
                "type": "array", "maxItems": 20,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["type", "message", "evidence_ids"],
                    "properties": {
                        "type": {"type": "string", "maxLength": 80},
                        "message": {"type": "string", "maxLength": 500},
                        "evidence_ids": {
                            "type": "array", "maxItems": 20, "items": evidence_id_schema,
                        },
                    },
                },
            },
        },
    }


def _codex_command_prefix(explicit: str | None) -> list[str] | None:
    if explicit:
        return [explicit]

    npm_wrapper = shutil.which("codex.cmd")
    if npm_wrapper:
        npm_root = Path(npm_wrapper).parent
        script = npm_root / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        node = npm_root / "node.exe"
        node_command = str(node) if node.exists() else shutil.which("node.exe") or shutil.which("node")
        if script.exists() and node_command:
            return [str(node_command), str(script)]

    executable = shutil.which("codex.exe")
    return [executable] if executable else None
