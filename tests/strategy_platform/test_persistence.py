from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from src.ai_trader.db.connection import connect
from src.ai_trader.db.migrations import init_database
from src.ai_trader.strategies import build_builtin_registry
from src.ai_trader.strategy_platform.pipeline import StrategyPipeline
from src.ai_trader.strategy_platform.repositories import save_analysis_session, save_pipeline_result
from src.ai_trader.web import services
from src.ai_trader.web.settings import build_settings

from tests.strategy_platform.helpers import FIXED_CLOCK, fixed_holding_snapshot, fixed_snapshot


class PersistenceTests(unittest.TestCase):
    def test_pipeline_and_session_are_visible_in_existing_decision_services(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "data"
            migration = init_database(output_root)
            db_path = Path(str(migration["db_path"]))
            pipeline = StrategyPipeline(build_builtin_registry(), clock=FIXED_CLOCK)
            buy = pipeline.run(fixed_snapshot())
            holding = pipeline.run(fixed_holding_snapshot())
            analysis_id = "sas_000001_test"
            payload = {
                "schema_version": "strategy_analysis_session.v0.2",
                "analysis_id": analysis_id,
                "symbol": "000001",
                "name": "测试股票",
                "trade_date": "2026-07-06",
                "decision_time": buy.snapshot.decision_time,
                "source": "unit_test",
                "has_position": True,
                "buy": buy.to_dict(),
                "holding": holding.to_dict(),
            }
            session = {
                "analysis_id": analysis_id,
                "symbol": "000001",
                "name": "测试股票",
                "trade_date": "2026-07-06",
                "decision_time": buy.snapshot.decision_time,
                "source": "unit_test",
                "has_position": True,
                "buy_snapshot_id": buy.snapshot.snapshot_id,
                "buy_run_id": buy.run.run_id,
                "buy_aggregation_id": buy.aggregation.aggregation_id,
                "buy_conclusion": buy.aggregation.conclusion.value,
                "holding_snapshot_id": holding.snapshot.snapshot_id,
                "holding_run_id": holding.run.run_id,
                "holding_aggregation_id": holding.aggregation.aggregation_id,
                "holding_conclusion": holding.aggregation.conclusion.value,
                "effective_strategy_count": 2,
                "blocked_strategy_count": 1,
                "failed_strategy_count": 0,
                "status": "COMPLETED",
                "report_relative_path": "reports/test.md",
                "payload": payload,
            }
            with closing(connect(db_path)) as conn:
                save_pipeline_result(conn, buy)
                save_pipeline_result(conn, holding)
                save_analysis_session(conn, session)
                conn.commit()
                self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM strategy_runs").fetchone()[0])
                self.assertEqual(10, conn.execute("SELECT COUNT(*) FROM strategy_evaluations").fetchone()[0])
                self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM strategy_aggregations").fetchone()[0])

            settings = build_settings(output_dir=str(output_root), db_path=str(db_path))
            items = services.decisions(settings, symbol="000001")
            self.assertEqual(analysis_id, items[0]["decision_id"])
            self.assertTrue(items[0]["is_strategy_platform"])
            detail = services.decision_detail(settings, analysis_id)
            self.assertIsNotNone(detail)
            self.assertEqual(["持仓策略", "买入 / 加仓策略"], [item["title"] for item in detail["strategy_sections"]])


if __name__ == "__main__":
    unittest.main()
