"""SQLite persistence for v0.2 strategy platform results."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..db.repositories import bool_int, json_text, upsert
from .pipeline import PipelineResult


def save_pipeline_result(conn: sqlite3.Connection, result: PipelineResult) -> None:
    snapshot = result.snapshot
    run = result.run
    aggregation = result.aggregation
    upsert(
        conn,
        "analysis_snapshots",
        {
            "snapshot_id": snapshot.snapshot_id,
            "symbol": snapshot.symbol,
            "name": snapshot.name,
            "task_type": snapshot.task_type.value,
            "market_phase": snapshot.market_phase.value,
            "trade_date": snapshot.trade_date,
            "decision_time": snapshot.decision_time,
            "source_cutoff_time": snapshot.source_cutoff_time,
            "feature_set_version": snapshot.feature_set_version,
            "data_status": snapshot.data_quality.get("status"),
            "payload_json": json_text(snapshot.to_dict()),
            "created_at": snapshot.decision_time,
        },
        ["snapshot_id"],
    )
    errors = [
        {"strategy_id": item.metadata.strategy_id, "error": item.error}
        for item in run.evaluations
        if item.error
    ]
    upsert(
        conn,
        "strategy_runs",
        {
            "run_id": run.run_id,
            "snapshot_id": snapshot.snapshot_id,
            "symbol": snapshot.symbol,
            "task_type": snapshot.task_type.value,
            "market_phase": snapshot.market_phase.value,
            "status": run.status.value,
            "registry_version": run.registry_version,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "duration_ms": run.duration_ms,
            "error_json": json_text(errors),
            "payload_json": json_text(run.to_dict()),
        },
        ["run_id"],
    )
    for evaluation in run.evaluations:
        metadata = evaluation.metadata
        upsert(
            conn,
            "strategy_evaluations",
            {
                "evaluation_id": evaluation.evaluation_id,
                "run_id": run.run_id,
                "snapshot_id": snapshot.snapshot_id,
                "strategy_id": metadata.strategy_id,
                "strategy_version": metadata.strategy_version,
                "parameter_version": metadata.parameter_version,
                "strategy_family": metadata.strategy_family,
                "implementation_type": metadata.implementation_type.value,
                "maturity": metadata.maturity.value,
                "calibration_status": metadata.calibration_status.value,
                "applicable": bool_int(evaluation.applicable),
                "data_status": evaluation.data_status.value,
                "raw_score": evaluation.raw_score,
                "calibrated_score": evaluation.calibrated_score,
                "signal": evaluation.signal.value,
                "confidence": evaluation.confidence.value,
                "duration_ms": evaluation.duration_ms,
                "scoring_config_hash": evaluation.scoring_config_hash,
                "error_message": evaluation.error,
                "payload_json": json_text(evaluation.to_dict()),
                "created_at": evaluation.finished_at,
            },
            ["evaluation_id"],
        )
    upsert(
        conn,
        "strategy_aggregations",
        {
            "aggregation_id": aggregation.aggregation_id,
            "run_id": run.run_id,
            "snapshot_id": snapshot.snapshot_id,
            "task_type": snapshot.task_type.value,
            "conclusion": aggregation.conclusion.value,
            "effective_strategy_count": aggregation.effective_strategy_count,
            "support_count": aggregation.support_count,
            "oppose_count": aggregation.oppose_count,
            "neutral_count": aggregation.neutral_count,
            "unknown_count": aggregation.unknown_count,
            "blocked_strategy_count": len(aggregation.blocked_strategies),
            "failed_strategy_count": len(aggregation.failed_strategies),
            "aggregator_version": aggregation.aggregator_version,
            "payload_json": json_text(aggregation.to_dict()),
            "created_at": run.finished_at,
        },
        ["aggregation_id"],
    )


def save_analysis_session(conn: sqlite3.Connection, session: dict[str, Any]) -> None:
    upsert(
        conn,
        "strategy_analysis_sessions",
        {
            "analysis_id": session["analysis_id"],
            "symbol": session["symbol"],
            "name": session.get("name"),
            "trade_date": session["trade_date"],
            "decision_time": session["decision_time"],
            "source": session["source"],
            "has_position": bool_int(session["has_position"]),
            "buy_snapshot_id": session["buy_snapshot_id"],
            "buy_run_id": session["buy_run_id"],
            "buy_aggregation_id": session["buy_aggregation_id"],
            "buy_conclusion": session["buy_conclusion"],
            "holding_snapshot_id": session.get("holding_snapshot_id"),
            "holding_run_id": session.get("holding_run_id"),
            "holding_aggregation_id": session.get("holding_aggregation_id"),
            "holding_conclusion": session.get("holding_conclusion"),
            "effective_strategy_count": session["effective_strategy_count"],
            "blocked_strategy_count": session["blocked_strategy_count"],
            "failed_strategy_count": session["failed_strategy_count"],
            "status": session["status"],
            "report_relative_path": session.get("report_relative_path"),
            "payload_json": json_text(session["payload"]),
            "created_at": session["decision_time"],
        },
        ["analysis_id"],
    )
