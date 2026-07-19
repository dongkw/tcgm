from __future__ import annotations

import unittest

from src.ai_trader.strategy_platform.contracts import BuySignal, RunStatus
from src.ai_trader.strategy_platform.registry import StrategyRegistry
from src.ai_trader.strategy_platform.runner import StrategyRunner

from tests.strategy_platform.helpers import (
    FIXED_CLOCK,
    FailingStrategy,
    PassingStrategy,
    fixed_snapshot,
    metadata,
    scoring_config,
)


class RunnerTests(unittest.TestCase):
    def test_missing_feature_blocks_only_that_strategy(self) -> None:
        missing_meta = metadata("missing-feature", required_features=("technical.not_here",))
        strategy = PassingStrategy(missing_meta)
        registry = StrategyRegistry()
        registry.register(strategy, scoring_config(missing_meta))

        result = StrategyRunner(registry, clock=FIXED_CLOCK).run(fixed_snapshot())

        self.assertEqual(RunStatus.COMPLETED, result.status)
        self.assertEqual(0, strategy.calls)
        self.assertIsNone(result.evaluations[0].raw_score)
        self.assertEqual(BuySignal.UNKNOWN, result.evaluations[0].signal)
        self.assertIn("technical.not_here", result.evaluations[0].risks[0]["features"])

    def test_strategy_failure_is_isolated(self) -> None:
        good_meta = metadata("good-strategy")
        bad_meta = metadata("bad-strategy")
        registry = StrategyRegistry()
        registry.register(PassingStrategy(good_meta), scoring_config(good_meta))
        registry.register(FailingStrategy(bad_meta), scoring_config(bad_meta))

        result = StrategyRunner(registry, clock=FIXED_CLOCK).run(fixed_snapshot())

        self.assertEqual(RunStatus.PARTIAL, result.status)
        by_id = {item.metadata.strategy_id: item for item in result.evaluations}
        self.assertEqual(80.0, by_id["good-strategy"].raw_score)
        self.assertIn("intentional failure", by_id["bad-strategy"].error)

    def test_repeated_runs_keep_same_business_result(self) -> None:
        meta = metadata()
        registry = StrategyRegistry()
        registry.register(PassingStrategy(meta), scoring_config(meta))
        runner = StrategyRunner(registry, clock=FIXED_CLOCK)

        first = runner.run(fixed_snapshot()).evaluations[0]
        second = runner.run(fixed_snapshot()).evaluations[0]

        self.assertEqual(first.raw_score, second.raw_score)
        self.assertEqual(first.signal, second.signal)
        self.assertEqual(first.rule_results, second.rule_results)
        self.assertEqual(first.scoring_config_hash, second.scoring_config_hash)


if __name__ == "__main__":
    unittest.main()
