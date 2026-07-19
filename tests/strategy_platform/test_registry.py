from __future__ import annotations

import unittest

from src.ai_trader.strategy_platform.registry import StrategyRegistry
from src.ai_trader.strategy_platform.validation import ContractValidationError

from tests.strategy_platform.helpers import PassingStrategy, metadata, scoring_config


class RegistryTests(unittest.TestCase):
    def test_duplicate_enabled_strategy_id_is_rejected(self) -> None:
        registry = StrategyRegistry()
        first_meta = metadata()
        registry.register(PassingStrategy(first_meta), scoring_config(first_meta))

        second_meta = metadata()
        with self.assertRaises(ContractValidationError):
            registry.register(PassingStrategy(second_meta), scoring_config(second_meta))

    def test_registry_version_is_stable(self) -> None:
        first = StrategyRegistry()
        second = StrategyRegistry()
        meta = metadata()
        first.register(PassingStrategy(meta), scoring_config(meta))
        second.register(PassingStrategy(meta), scoring_config(meta))
        self.assertEqual(first.version, second.version)


if __name__ == "__main__":
    unittest.main()
