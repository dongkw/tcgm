from __future__ import annotations

import unittest
from dataclasses import replace

from src.ai_trader.strategies import build_builtin_registry
from src.ai_trader.ai_interface.providers import ManualProvider
from src.ai_trader.strategy_platform.contracts import (
    AggregationConclusion,
    BuySignal,
    HoldingSignal,
    freeze_json,
    thaw_json,
)
from src.ai_trader.strategy_platform.pipeline import StrategyPipeline
from src.ai_trader.strategy_platform.report import render_strategy_report

from tests.strategy_platform.helpers import fixed_holding_snapshot, fixed_snapshot


class BuiltinStrategyTests(unittest.TestCase):
    def test_builtin_registry_has_ten_independent_strategies(self) -> None:
        entries = build_builtin_registry().entries()
        self.assertEqual(
            {
                "trend-following", "technical-exit", "ai-research",
                "pretrade-veto", "fundamental-gate", "valuation-discipline",
                "chip-risk-gate", "thesis-completeness", "holding-thesis-exit",
                "horizon-fit-gate",
            },
            {entry.metadata.strategy_id for entry in entries},
        )

    def test_trend_strategy_supports_fixed_rising_sample(self) -> None:
        result = StrategyPipeline(build_builtin_registry()).run(fixed_snapshot())
        evaluations = {item.metadata.strategy_id: item for item in result.run.evaluations}

        self.assertEqual(100.0, evaluations["trend-following"].raw_score)
        self.assertEqual(BuySignal.STRONG_SUPPORT, evaluations["trend-following"].signal)
        self.assertEqual(BuySignal.UNKNOWN, evaluations["ai-research"].signal)
        self.assertEqual(AggregationConclusion.FAVORABLE, result.aggregation.conclusion)

    def test_technical_exit_does_not_apply_t_plus_one(self) -> None:
        result = StrategyPipeline(build_builtin_registry()).run(fixed_holding_snapshot())
        evaluation = next(item for item in result.run.evaluations if item.metadata.strategy_id == "technical-exit")

        self.assertEqual("technical-exit", evaluation.metadata.strategy_id)
        self.assertEqual(HoldingSignal.EXIT_SUPPORT, evaluation.signal)
        self.assertTrue(any(item.get("type") == "EXECUTION_NOT_CHECKED" for item in evaluation.risks))

    def test_valid_ai_evidence_is_scored_without_ai_score(self) -> None:
        def response(request):
            evidence = next(item for item in request.evidence if item.path == "features.technical.ma20")
            return {
                "schema_version": "ai_research_response.v1",
                "request_id": request.request_id,
                "task": request.task.value,
                "task_version": request.task_version,
                "provider": "manual",
                "stance": "SUPPORT",
                "confidence": "LOW",
                "summary": "趋势证据支持继续研究，但不构成交易动作",
                "evidence_refs": [
                    {"evidence_id": evidence.evidence_id, "polarity": "SUPPORTING", "message": "MA20"}
                ],
                "risks": [{"type": "AI_UNCALIBRATED", "message": "AI 尚未校准", "evidence_ids": [evidence.evidence_id]}],
            }

        result = StrategyPipeline(build_builtin_registry(ai_provider=ManualProvider(response))).run(fixed_snapshot())
        evaluation = next(item for item in result.run.evaluations if item.metadata.strategy_id == "ai-research")

        self.assertIsNone(evaluation.error)
        self.assertEqual(70.0, evaluation.raw_score)
        self.assertEqual(BuySignal.SUPPORT, evaluation.signal)

    def test_fabricated_ai_evidence_is_rejected_and_isolated(self) -> None:
        def response(request):
            return {
                "schema_version": "ai_research_response.v1",
                "request_id": request.request_id,
                "task": request.task.value,
                "task_version": request.task_version,
                "provider": "manual",
                "stance": "OPPOSE",
                "confidence": "HIGH",
                "summary": "伪造字段测试",
                "evidence_refs": [
                    {"evidence_id": "ev_fabricated", "polarity": "OPPOSING", "message": "错误证据"}
                ],
                "risks": [],
            }

        result = StrategyPipeline(build_builtin_registry(ai_provider=ManualProvider(response))).run(fixed_snapshot())
        evaluation = next(item for item in result.run.evaluations if item.metadata.strategy_id == "ai-research")

        self.assertIsNone(evaluation.error)
        self.assertEqual("BLOCKED", evaluation.data_status.value)
        self.assertTrue(any("unknown evidence_id" in item.get("message", "") for item in evaluation.risks))
        self.assertEqual(BuySignal.UNKNOWN, evaluation.signal)
        self.assertEqual("COMPLETED", result.run.status.value)

    def test_cross_family_conflict_is_visible_in_report(self) -> None:
        def response(request):
            evidence = next(item for item in request.evidence if item.path == "facts.quote.price")
            return {
                "schema_version": "ai_research_response.v1",
                "request_id": request.request_id,
                "task": request.task.value,
                "task_version": request.task_version,
                "provider": "manual",
                "stance": "OPPOSE",
                "confidence": "LOW",
                "summary": "AI 暂时反对",
                "evidence_refs": [
                    {"evidence_id": evidence.evidence_id, "polarity": "OPPOSING", "message": "当前价格证据"}
                ],
                "risks": [],
            }

        result = StrategyPipeline(build_builtin_registry(ai_provider=ManualProvider(response))).run(fixed_snapshot())
        report = render_strategy_report(result)

        self.assertEqual(AggregationConclusion.MIXED, result.aggregation.conclusion)
        self.assertTrue(any(item.get("type") == "INTER_FAMILY" for item in result.aggregation.conflicts))
        self.assertIn("不同策略家族存在方向冲突", report)
        self.assertNotIn("综合评分", report)
        self.assertIn("尚未生成未经校准的统一总分", report)

    def test_hard_veto_overrides_positive_trend(self) -> None:
        snapshot = fixed_snapshot()
        features = thaw_json(snapshot.features)
        features["engine_flags"]["a2_deducted_profit_negative_2y"] = True
        vetoed_snapshot = replace(snapshot, features=freeze_json(features))

        result = StrategyPipeline(build_builtin_registry()).run(vetoed_snapshot)
        evaluations = {item.metadata.strategy_id: item for item in result.run.evaluations}

        self.assertEqual(BuySignal.STRONG_SUPPORT, evaluations["trend-following"].signal)
        self.assertEqual(BuySignal.STRONG_OPPOSE, evaluations["pretrade-veto"].signal)
        self.assertEqual(AggregationConclusion.UNFAVORABLE, result.aggregation.conclusion)
        self.assertTrue(any(item.get("type") == "VETO" for item in result.aggregation.conflicts))

    def test_blocked_veto_prevents_favorable_buy_conclusion(self) -> None:
        snapshot = fixed_snapshot()
        features = thaw_json(snapshot.features)
        features["ownership"]["shareholder_count"] = {}
        blocked_snapshot = replace(snapshot, features=freeze_json(features))

        result = StrategyPipeline(build_builtin_registry()).run(blocked_snapshot)

        self.assertEqual(AggregationConclusion.INSUFFICIENT, result.aggregation.conclusion)
        self.assertIn("chip-risk-gate", result.aggregation.blocked_strategies)
        self.assertTrue(any(item.get("type") == "VETO_DATA_BLOCKED" for item in result.aggregation.conflicts))


if __name__ == "__main__":
    unittest.main()
