from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from src.ai_trader.db.connection import connect
from src.ai_trader.db.migrations import init_database
from src.ai_trader.db.strategy_contexts import (
    get_strategy_context,
    list_strategy_context_revisions,
    normalize_strategy_context,
    save_strategy_context,
)


class StrategyContextTests(unittest.TestCase):
    def test_context_is_current_and_every_save_creates_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            migration = init_database(Path(tmp) / "data")
            db_path = Path(str(migration["db_path"]))
            first = {
                "holding_period": "middle",
                "stock_type": "GROWTH",
                "future_eps": "1.25",
                "high_risk": "false",
                "core_logic": "产品升级带动利润增长",
                "logic_still_valid": "true",
                "thesis_fully_realized": "false",
                "would_rebuy_now": "true",
            }
            second = {**first, "future_eps": "1.35", "would_rebuy_now": "false"}

            with closing(connect(db_path)) as conn:
                save_strategy_context(conn, "000001", first)
                save_strategy_context(conn, "000001", second)
                conn.commit()
                current = get_strategy_context(conn, "000001")
                revisions = list_strategy_context_revisions(conn, "000001")
                profile_count = conn.execute("SELECT COUNT(*) FROM strategy_context_profiles").fetchone()[0]

            self.assertEqual(1, profile_count)
            self.assertEqual(2, len(revisions))
            self.assertEqual(1.35, current["future_eps"])
            self.assertFalse(current["high_risk"])
            self.assertFalse(current["would_rebuy_now"])

    def test_invalid_positive_eps_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_strategy_context("000001", {"future_eps": "0"})

    def test_invalid_symbol_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_strategy_context("001", {})


if __name__ == "__main__":
    unittest.main()
