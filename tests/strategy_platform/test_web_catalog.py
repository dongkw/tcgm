from __future__ import annotations

import unittest

from src.ai_trader.web.services import strategy_catalog


class StrategyCatalogTests(unittest.TestCase):
    def test_catalog_is_derived_from_registered_strategy_code(self) -> None:
        catalog = strategy_catalog()
        strategies = {item["strategy_id"]: item for item in catalog["strategies"]}

        self.assertEqual(10, catalog["strategy_count"])
        self.assertEqual(
            {
                "trend-following", "technical-exit", "ai-research",
                "pretrade-veto", "fundamental-gate", "valuation-discipline",
                "chip-risk-gate", "thesis-completeness", "holding-thesis-exit",
                "horizon-fit-gate",
            },
            set(strategies),
        )
        trend = strategies["trend-following"]
        self.assertEqual("src/ai_trader/strategies/trend_following/strategy.py", trend["implementation_path"])
        self.assertEqual("src/ai_trader/strategies/trend_following/scoring.json", trend["scoring_path"])
        self.assertIn("TF_PRICE_MA20", {rule["rule_id"] for rule in trend["rule_weights"]})


if __name__ == "__main__":
    unittest.main()
