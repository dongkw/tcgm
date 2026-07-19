from __future__ import annotations

import json
import unittest

from src.ai_trader.analysis_snapshot import analysis_snapshot_from_dict, build_analysis_snapshot
from src.ai_trader.strategy_platform.contracts import MarketPhase, TaskType
from src.ai_trader.strategy_platform.validation import (
    ContractValidationError,
    validate_metadata,
)

from tests.strategy_platform.helpers import FIXTURES, fixed_snapshot, metadata


class ContractTests(unittest.TestCase):
    def test_snapshot_is_immutable_and_round_trips(self) -> None:
        snapshot = fixed_snapshot()
        with self.assertRaises(TypeError):
            snapshot.features["technical"]["ma20"] = 0

        restored = analysis_snapshot_from_dict(snapshot.to_dict())
        self.assertEqual(snapshot.to_dict(), restored.to_dict())

    def test_invalid_strategy_id_is_rejected(self) -> None:
        invalid = metadata("Bad_ID")
        with self.assertRaises(ContractValidationError):
            validate_metadata(invalid)

    def test_user_future_eps_resolves_raw_missing_forecast_warning(self) -> None:
        raw = json.loads((FIXTURES / "rising_stock.json").read_text(encoding="utf-8"))
        raw["data_gaps_for_engine"] = ["未来第3年EPS预估及假设需另行补充"]
        time_context = {
            "trade_date": "2026-07-06",
            "decision_time": "2026-07-06T16:00:00+08:00",
            "effective_data_cutoff": "2026-07-06T16:00:00+08:00",
            "session_name": "POST_MARKET",
            "time_warnings": [],
        }

        snapshot = build_analysis_snapshot(
            raw,
            time_context,
            TaskType.BUY,
            market_phase=MarketPhase.POST_MARKET,
            user_context={"future_eps": 1.3},
        )

        self.assertNotIn(
            "未来第3年EPS预估及假设需另行补充",
            snapshot.data_quality["warnings"],
        )


if __name__ == "__main__":
    unittest.main()
