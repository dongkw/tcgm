from __future__ import annotations

import unittest

from src.ai_trader.strategy_platform.contracts import BuySignal
from src.ai_trader.strategy_platform.scoring import score_evidence

from tests.strategy_platform.helpers import PassingStrategy, fixed_snapshot, metadata, scoring_config


class ScoringTests(unittest.TestCase):
    def test_rule_score_and_signal_are_deterministic(self) -> None:
        meta = metadata()
        evidence = PassingStrategy(meta).evaluate(fixed_snapshot())
        result = score_evidence(meta, evidence, scoring_config(meta))

        self.assertEqual(80.0, result.raw_score)
        self.assertEqual(BuySignal.STRONG_SUPPORT, result.signal)
        self.assertEqual(30.0, result.details[0]["delta"])


if __name__ == "__main__":
    unittest.main()
