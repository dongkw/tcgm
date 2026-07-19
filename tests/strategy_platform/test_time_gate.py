from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from src.ai_trader.analysis_snapshot import build_analysis_snapshot
from src.ai_trader.strategy_platform.contracts import DataStatus, TaskType
from src.ai_trader.timekeeper import build_time_context


TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def market_payload(*, quote_date: str | None, kline_date: str | None) -> dict:
    technical = {
        "ma20": 10.0,
        "ma60": 9.0,
        "high_20d": 11.0,
        "low_20d": 8.0,
    }
    if kline_date:
        technical.update(
            {
                "last_date": kline_date,
                "recent_10d": [{"date": kline_date, "close": 10.0}],
            }
        )
    return {
        "quote": {
            "code": "000001",
            "name": "test stock",
            "market": "SZ",
            "trade_date": quote_date,
            "price": 10.0,
        },
        "technical": technical,
    }


class TimeGateTests(unittest.TestCase):
    def test_weekend_uses_latest_completed_trade_date_for_research_only(self) -> None:
        raw = market_payload(quote_date="2026-07-17", kline_date="2026-07-17")
        context = build_time_context(
            raw,
            TaskType.BUY.value,
            now=datetime(2026, 7, 18, 16, 0, tzinfo=TZ),
        )

        self.assertEqual("2026-07-17", context["trade_date"])
        self.assertNotEqual(context["calendar_date"], context["trade_date"])
        self.assertTrue(context["generated_at"].startswith("2026-07-18T16:00:00"))
        self.assertFalse(context["is_trading_day"])
        self.assertEqual(DataStatus.GOOD.value, context["data_status"])
        self.assertEqual([], context["blocked_data_reasons"])
        self.assertTrue(context["time_warnings"])
        snapshot = build_analysis_snapshot(raw, context, TaskType.BUY)
        self.assertEqual(DataStatus.USABLE.value, snapshot.data_quality["status"])

    def test_weekend_date_falsely_written_as_quote_trade_date_is_blocked(self) -> None:
        raw = market_payload(quote_date="2026-07-18", kline_date="2026-07-17")
        context = build_time_context(
            raw,
            TaskType.BUY.value,
            now=datetime(2026, 7, 18, 16, 0, tzinfo=TZ),
        )

        self.assertEqual("2026-07-17", context["trade_date"])
        self.assertEqual(DataStatus.BLOCKED.value, context["data_status"])
        self.assertIn(
            "quote.trade_date is not a valid A-share trading date",
            context["blocked_data_reasons"],
        )

    def test_latest_effective_trade_date_is_good_on_matching_trading_day(self) -> None:
        raw = market_payload(quote_date="2026-07-17", kline_date="2026-07-17")
        context = build_time_context(
            raw,
            TaskType.BUY.value,
            now=datetime(2026, 7, 17, 16, 0, tzinfo=TZ),
        )

        self.assertEqual("2026-07-17", context["trade_date"])
        self.assertEqual(DataStatus.GOOD.value, context["data_status"])
        self.assertEqual([], context["blocked_data_reasons"])
        snapshot = build_analysis_snapshot(raw, context, TaskType.BUY)
        self.assertEqual(DataStatus.GOOD.value, snapshot.data_quality["status"])

    def test_missing_effective_market_date_is_blocked(self) -> None:
        raw = market_payload(quote_date=None, kline_date=None)
        context = build_time_context(
            raw,
            TaskType.BUY.value,
            now=datetime(2026, 7, 17, 16, 0, tzinfo=TZ),
        )

        self.assertIsNone(context["trade_date"])
        self.assertEqual(DataStatus.BLOCKED.value, context["data_status"])
        self.assertIn(
            "no valid quote or daily K-line trade date is available",
            context["blocked_data_reasons"],
        )

    def test_stale_market_date_is_blocked(self) -> None:
        raw = market_payload(quote_date="2026-07-06", kline_date="2026-07-06")
        context = build_time_context(
            raw,
            TaskType.BUY.value,
            now=datetime(2026, 7, 19, 10, 0, tzinfo=TZ),
        )

        self.assertEqual(13, context["market_data_age_days"])
        self.assertEqual(DataStatus.BLOCKED.value, context["data_status"])
        self.assertIn(
            "latest effective market date is stale by 13 calendar days",
            context["blocked_data_reasons"],
        )

    def test_future_market_date_is_blocked(self) -> None:
        raw = market_payload(quote_date="2026-07-20", kline_date="2026-07-20")
        context = build_time_context(
            raw,
            TaskType.BUY.value,
            now=datetime(2026, 7, 19, 10, 0, tzinfo=TZ),
        )

        self.assertEqual(-1, context["market_data_age_days"])
        self.assertIn(
            "latest effective market date is in the future",
            context["blocked_data_reasons"],
        )


if __name__ == "__main__":
    unittest.main()
