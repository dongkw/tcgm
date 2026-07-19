from __future__ import annotations

import json
import subprocess
import unittest
from dataclasses import replace
from time import sleep

from src.ai_trader.ai_interface.contracts import AIContractError, AIProviderUnavailable, AITask
from src.ai_trader.ai_interface.providers import (
    CodexCliProvider,
    CodexProvider,
    ManualProvider,
    ProviderRegistry,
)
from src.ai_trader.ai_interface.service import AIResearchService, build_request
from src.ai_trader.strategies.ai_research.strategy import AIResearchStrategy

from tests.strategy_platform.helpers import fixed_snapshot


def valid_response(request, *, provider="manual"):
    evidence = request.evidence[0]
    return {
        "schema_version": "ai_research_response.v1",
        "request_id": request.request_id,
        "task": request.task.value,
        "task_version": request.task_version,
        "provider": provider,
        "summary": "仅总结已提供证据，不生成交易动作",
        "confidence": "LOW",
        "stance": "NEUTRAL",
        "evidence_refs": [{
            "evidence_id": evidence.evidence_id,
            "polarity": "NEUTRAL",
            "message": "固定证据引用",
        }],
        "risks": [],
    }


class AIInterfaceTests(unittest.TestCase):
    def test_unknown_task_is_rejected_before_provider_call(self) -> None:
        calls = []
        provider = ManualProvider(lambda request: calls.append(request) or valid_response(request))

        with self.assertRaisesRegex(AIContractError, "unknown AI task"):
            AIResearchService(provider).run("generate_order", fixed_snapshot())

        self.assertEqual([], calls)

    def test_request_without_evidence_is_rejected(self) -> None:
        snapshot = replace(fixed_snapshot(), facts={}, features={})

        with self.assertRaisesRegex(AIContractError, "at least one evidence"):
            AIResearchService(ManualProvider(valid_response)).run(AITask.RESEARCH_SUMMARY, snapshot)

    def test_response_with_buy_or_sell_field_is_rejected(self) -> None:
        def illegal_response(request):
            response = valid_response(request)
            response["buy"] = {"quantity": 100}
            return response

        with self.assertRaisesRegex(AIContractError, "forbidden trading field"):
            AIResearchService(ManualProvider(illegal_response)).run(
                AITask.RESEARCH_SUMMARY,
                fixed_snapshot(),
            )

    def test_provider_can_be_replaced_without_changing_service(self) -> None:
        class OtherProvider:
            name = "other-ai"

            def execute(self, request):
                return valid_response(request, provider=self.name)

        registry = ProviderRegistry()
        registry.register(CodexProvider())
        registry.register(OtherProvider())
        result = AIResearchService(registry.get("other-ai")).run(
            AITask.RESEARCH_SUMMARY,
            fixed_snapshot(),
        )

        self.assertEqual("other-ai", result.provider)
        self.assertEqual(AITask.RESEARCH_SUMMARY, result.task)

    def test_codex_without_explicit_executor_is_disabled(self) -> None:
        with self.assertRaisesRegex(AIProviderUnavailable, "current Codex conversation"):
            AIResearchService(CodexProvider()).run(AITask.RESEARCH_SUMMARY, fixed_snapshot())

    def test_codex_cli_uses_read_only_ephemeral_structured_execution(self) -> None:
        observed = {}

        def fake_runner(args, **kwargs):
            observed["args"] = args
            observed["cwd"] = kwargs["cwd"]
            request = json.loads(kwargs["input"].split("REQUEST=", 1)[1])
            evidence_id = request["evidence"][0]["evidence_id"]
            response = {
                "schema_version": "ai_research_response.v1",
                "request_id": request["request_id"],
                "task": request["task"],
                "task_version": request["task_version"],
                "provider": "codex",
                "summary": "只依据固定证据完成研究摘要",
                "confidence": "LOW",
                "stance": "NEUTRAL",
                "evidence_refs": [{
                    "evidence_id": evidence_id,
                    "polarity": "NEUTRAL",
                    "message": "已核对输入证据",
                }],
                "risks": [],
            }
            output_path = args[args.index("--output-last-message") + 1]
            with open(output_path, "w", encoding="utf-8") as stream:
                json.dump(response, stream, ensure_ascii=False)
            return subprocess.CompletedProcess(args, 0, "", "")

        provider = CodexCliProvider(executable="codex-test", runner=fake_runner)
        result = AIResearchService(provider).run(AITask.RESEARCH_SUMMARY, fixed_snapshot())

        self.assertEqual("codex", result.provider)
        self.assertIn("--ephemeral", observed["args"])
        self.assertIn("--ignore-rules", observed["args"])
        self.assertIn("--ignore-user-config", observed["args"])
        sandbox_index = observed["args"].index("--sandbox")
        self.assertEqual("read-only", observed["args"][sandbox_index + 1])
        self.assertIn("--output-schema", observed["args"])

    def test_provider_failure_blocks_strategy(self) -> None:
        class FailedProvider:
            name = "failed-ai"

            def execute(self, request):
                raise RuntimeError("provider unavailable")

        evidence = AIResearchStrategy(FailedProvider()).evaluate(fixed_snapshot())

        self.assertEqual("BLOCKED", evidence.data_status.value)
        self.assertIn("provider unavailable", evidence.risks[0]["message"])

    def test_timeout_blocks_strategy(self) -> None:
        class SlowProvider:
            name = "slow-ai"

            def execute(self, request):
                sleep(0.02)
                return valid_response(request, provider=self.name)

        evidence = AIResearchStrategy(SlowProvider(), timeout_seconds=0.001).evaluate(fixed_snapshot())

        self.assertEqual("BLOCKED", evidence.data_status.value)
        self.assertIn("timed out", evidence.risks[0]["message"])

    def test_request_excludes_undeclared_feature_paths(self) -> None:
        request = build_request(AITask.RESEARCH_SUMMARY, fixed_snapshot())
        paths = {item.path for item in request.evidence}

        self.assertNotIn("features.quote.price", paths)
        self.assertIn("features.technical.ma20", paths)
        self.assertIn("features.valuation.pe_ttm", paths)


if __name__ == "__main__":
    unittest.main()
