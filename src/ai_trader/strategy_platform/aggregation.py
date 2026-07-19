"""Transparent family-level aggregation without an uncalibrated overall score."""

from __future__ import annotations

import uuid
from collections import defaultdict

from .contracts import (
    AggregationRole,
    AggregationConclusion,
    AnalysisSnapshot,
    BuySignal,
    DataStatus,
    FamilyOpinion,
    HoldingSignal,
    Maturity,
    StrategyAggregation,
    StrategyEvaluation,
    StrategyRunResult,
    TaskType,
)


AGGREGATOR_VERSION = "family-consensus.v0.2.0"


def _direction(evaluation: StrategyEvaluation, task_type: TaskType) -> int | None:
    if evaluation.error or not evaluation.applicable or evaluation.data_status == DataStatus.BLOCKED:
        return None
    if task_type == TaskType.BUY:
        if evaluation.signal in {BuySignal.STRONG_SUPPORT, BuySignal.SUPPORT}:
            return 1
        if evaluation.signal in {BuySignal.OPPOSE, BuySignal.STRONG_OPPOSE}:
            return -1
        if evaluation.signal == BuySignal.NEUTRAL:
            return 0
        return None
    if evaluation.signal == HoldingSignal.HOLD_SUPPORT:
        return 1
    if evaluation.signal in {HoldingSignal.REDUCE_SUPPORT, HoldingSignal.EXIT_SUPPORT}:
        return -1
    return None


def aggregate_strategies(
    snapshot: AnalysisSnapshot,
    run: StrategyRunResult,
) -> StrategyAggregation:
    if snapshot.snapshot_id != run.snapshot_id:
        raise ValueError("strategy run does not belong to snapshot")

    directions: list[int] = []
    family_items: dict[str, list[tuple[StrategyEvaluation, int | None]]] = defaultdict(list)
    blocked: list[str] = []
    failed: list[str] = []
    vetoed: list[str] = []
    blocked_vetoes: list[str] = []
    maturity_summary = {item.value: 0 for item in Maturity}

    for evaluation in run.evaluations:
        maturity_summary[evaluation.metadata.maturity.value] += 1
        direction = _direction(evaluation, snapshot.task_type)
        family_items[evaluation.metadata.strategy_family].append((evaluation, direction))
        if evaluation.error:
            failed.append(evaluation.metadata.strategy_id)
        elif evaluation.data_status == DataStatus.BLOCKED:
            blocked.append(evaluation.metadata.strategy_id)
        if evaluation.metadata.aggregation_role == AggregationRole.VETO:
            if direction == -1:
                vetoed.append(evaluation.metadata.strategy_id)
            elif evaluation.error or evaluation.data_status == DataStatus.BLOCKED:
                blocked_vetoes.append(evaluation.metadata.strategy_id)
        if direction is not None:
            directions.append(direction)

    family_summary = []
    conflicts = []
    family_opinions: list[FamilyOpinion] = []
    for family in sorted(family_items):
        items = family_items[family]
        valid = [direction for _, direction in items if direction is not None]
        strategy_ids = [evaluation.metadata.strategy_id for evaluation, _ in items]
        if 1 in valid and -1 in valid:
            opinion = FamilyOpinion.CONFLICTED
            conflicts.append(
                {
                    "type": "INTRA_FAMILY",
                    "family": family,
                    "strategies": strategy_ids,
                    "message": "同一策略家族内部结论冲突",
                }
            )
        elif 1 in valid:
            opinion = FamilyOpinion.POSITIVE
        elif -1 in valid:
            opinion = FamilyOpinion.NEGATIVE
        elif 0 in valid:
            opinion = FamilyOpinion.NEUTRAL
        else:
            opinion = FamilyOpinion.UNKNOWN
        family_opinions.append(opinion)
        family_summary.append(
            {
                "family": family,
                "opinion": opinion.value,
                "strategies": strategy_ids,
                "effective_count": len(valid),
            }
        )

    positive_families = sum(item == FamilyOpinion.POSITIVE for item in family_opinions)
    negative_families = sum(item == FamilyOpinion.NEGATIVE for item in family_opinions)
    conflicted_families = sum(item == FamilyOpinion.CONFLICTED for item in family_opinions)
    if vetoed:
        conclusion = AggregationConclusion.UNFAVORABLE
        conflicts.append(
            {
                "type": "VETO",
                "strategies": vetoed,
                "message": "硬否决策略已触发，普通支持策略不能覆盖该结论",
            }
        )
    elif blocked_vetoes:
        conclusion = (
            AggregationConclusion.UNFAVORABLE
            if snapshot.task_type == TaskType.HOLDING and -1 in directions
            else AggregationConclusion.INSUFFICIENT
        )
        conflicts.append(
            {
                "type": "VETO_DATA_BLOCKED",
                "strategies": blocked_vetoes,
                "message": "硬否决闸门缺少关键数据，不能形成支持买入的综合结论",
            }
        )
    elif not directions:
        conclusion = AggregationConclusion.INSUFFICIENT
    elif conflicted_families or (positive_families and negative_families):
        conclusion = AggregationConclusion.MIXED
        if positive_families and negative_families:
            conflicts.append(
                {
                    "type": "INTER_FAMILY",
                    "message": "不同策略家族存在方向冲突",
                    "positive_family_count": positive_families,
                    "negative_family_count": negative_families,
                }
            )
    elif positive_families:
        conclusion = AggregationConclusion.FAVORABLE
    elif negative_families:
        conclusion = AggregationConclusion.UNFAVORABLE
    else:
        conclusion = AggregationConclusion.MIXED

    return StrategyAggregation(
        aggregation_id=f"sa_{snapshot.symbol}_{uuid.uuid4().hex}",
        run_id=run.run_id,
        snapshot_id=snapshot.snapshot_id,
        task_type=snapshot.task_type,
        conclusion=conclusion,
        effective_strategy_count=len(directions),
        support_count=sum(item == 1 for item in directions),
        oppose_count=sum(item == -1 for item in directions),
        neutral_count=sum(item == 0 for item in directions),
        unknown_count=len(run.evaluations) - len(directions),
        maturity_summary=maturity_summary,
        family_summary=tuple(family_summary),
        conflicts=tuple(conflicts),
        blocked_strategies=tuple(blocked),
        failed_strategies=tuple(failed),
        aggregator_version=AGGREGATOR_VERSION,
    )
