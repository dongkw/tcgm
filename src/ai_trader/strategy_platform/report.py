"""Render Chinese reports from persisted multi-strategy results."""

from __future__ import annotations

from pathlib import Path

from .contracts import (
    AggregationConclusion,
    BuySignal,
    HoldingSignal,
    StrategyEvaluation,
    TaskType,
    thaw_json,
)
from .pipeline import PipelineResult


BUY_CONCLUSION_LABELS = {
    AggregationConclusion.FAVORABLE: "有效策略整体支持",
    AggregationConclusion.MIXED: "策略结论存在冲突",
    AggregationConclusion.UNFAVORABLE: "有效策略整体反对",
    AggregationConclusion.INSUFFICIENT: "有效策略或数据不足",
}

HOLDING_CONCLUSION_LABELS = {
    AggregationConclusion.FAVORABLE: "有效策略整体支持继续持有",
    AggregationConclusion.MIXED: "持有与退出证据存在冲突",
    AggregationConclusion.UNFAVORABLE: "有效策略整体支持减仓或退出",
    AggregationConclusion.INSUFFICIENT: "有效持仓策略或数据不足",
}

SIGNAL_LABELS = {
    BuySignal.STRONG_SUPPORT: "强支持",
    BuySignal.SUPPORT: "支持",
    BuySignal.NEUTRAL: "中性",
    BuySignal.OPPOSE: "反对",
    BuySignal.STRONG_OPPOSE: "强反对",
    BuySignal.UNKNOWN: "未知",
    HoldingSignal.HOLD_SUPPORT: "支持持有",
    HoldingSignal.REDUCE_SUPPORT: "支持减仓",
    HoldingSignal.EXIT_SUPPORT: "支持退出",
    HoldingSignal.UNKNOWN: "未知",
}


def render_strategy_report(result: PipelineResult) -> str:
    snapshot = result.snapshot
    run = result.run
    aggregation = result.aggregation
    task_label = "买入研究" if snapshot.task_type == TaskType.BUY else "持仓研究"
    conclusion_labels = BUY_CONCLUSION_LABELS if snapshot.task_type == TaskType.BUY else HOLDING_CONCLUSION_LABELS
    quality = snapshot.data_quality
    lines = [
        f"# 多策略技术报告 {snapshot.symbol} {snapshot.name or ''}".rstrip(),
        "",
        "## 基础信息",
        f"- 任务：{task_label} (`{snapshot.task_type.value}`)",
        f"- 交易日：{snapshot.trade_date}",
        f"- 决策时间：{snapshot.decision_time}",
        f"- 数据截止：{snapshot.source_cutoff_time}",
        f"- 市场阶段：{snapshot.market_phase.value}",
        "",
        "## 数据状态",
        f"- 状态：{quality.get('status')}",
        f"- 阻塞字段：{list(quality.get('blocking_missing_fields') or [])}",
        f"- 时间和数据提示：{list(quality.get('warnings') or [])}",
        "",
        "## 综合研究结论",
        f"- 结论：{conclusion_labels[aggregation.conclusion]} (`{aggregation.conclusion.value}`)",
        f"- 有效策略：{aggregation.effective_strategy_count}",
        f"- 支持 / 反对 / 中性 / 未知：{aggregation.support_count} / {aggregation.oppose_count} / {aggregation.neutral_count} / {aggregation.unknown_count}",
        "- 说明：当前仅展示各策略原始分和一致性，尚未生成未经校准的统一总分。",
        "",
        "## 策略明细",
    ]
    if not run.evaluations:
        lines.append("- 当前任务和市场阶段没有已启用策略。")
    for evaluation in run.evaluations:
        lines.extend(_evaluation_lines(evaluation))

    lines.extend(["", "## 策略家族与冲突"])
    for family in aggregation.family_summary:
        lines.append(
            f"- {family.get('family')}：{family.get('opinion')}，策略 {list(family.get('strategies') or [])}"
        )
    if aggregation.conflicts:
        for conflict in aggregation.conflicts:
            lines.append(f"- 冲突：{conflict.get('message')} ({conflict.get('type')})")
    else:
        lines.append("- 未识别到有效策略之间的方向冲突。")

    lines.extend(
        [
            "",
            "## 版本与审计",
            f"- 快照版本：{snapshot.schema_version}",
            f"- 特征集版本：{snapshot.feature_set_version}",
            f"- 注册表版本：{run.registry_version}",
            f"- 汇总器版本：{aggregation.aggregator_version}",
            f"- 运行状态：{run.status.value}",
            "",
            "## 使用边界",
            "- 本报告是策略研究结果，不是成交指令。",
            "- `DRAFT` 和 `PAPER_ONLY` 策略不能直接用于实盘裁决。",
            "- 仓位、资金、T+1 和交易限制需要由后续风险与执行层处理。",
        ]
    )
    return "\n".join(lines) + "\n"


def _evaluation_lines(evaluation: StrategyEvaluation) -> list[str]:
    meta = evaluation.metadata
    score = "不评分" if evaluation.raw_score is None else str(evaluation.raw_score)
    lines = [
        "",
        f"### {meta.name} (`{meta.strategy_id}`)",
        f"- 家族：{meta.strategy_family}",
        f"- 汇总角色：{meta.aggregation_role.value}",
        f"- 实现 / 成熟度 / 校准：{meta.implementation_type.value} / {meta.maturity.value} / {meta.calibration_status.value}",
        f"- 信号：{SIGNAL_LABELS[evaluation.signal]} (`{evaluation.signal.value}`)",
        f"- 原始分：{score}",
        f"- 数据状态 / 信心：{evaluation.data_status.value} / {evaluation.confidence.value}",
        f"- 适用性：{evaluation.applicable}，{evaluation.applicability_reason}",
    ]
    if evaluation.error:
        lines.append(f"- 执行异常：{evaluation.error}")
    lines.append("- 规则：")
    if not evaluation.rule_results:
        lines.append("  - 无有效规则结果。")
    for rule in evaluation.rule_results:
        lines.append(f"  - {rule.rule_id} `{rule.status.value}`：{rule.message}；证据 {thaw_json(rule.evidence)}")
    lines.append(f"- 支持证据：{thaw_json(evaluation.supporting_evidence)}")
    lines.append(f"- 反对证据：{thaw_json(evaluation.opposing_evidence)}")
    lines.append(f"- 风险：{thaw_json(evaluation.risks)}")
    lines.append(f"- 触发条件：{thaw_json(evaluation.trigger_conditions)}")
    lines.append(f"- 证伪条件：{thaw_json(evaluation.invalidation_conditions)}")
    return lines


def write_strategy_report(path: Path, result: PipelineResult) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_strategy_report(result), encoding="utf-8")
    return path


def render_analysis_session_report(
    buy_result: PipelineResult,
    holding_result: PipelineResult | None = None,
) -> str:
    """Render one user-facing report while keeping buy and holding pipelines separate."""
    snapshot = buy_result.snapshot
    lines = [
        f"# 多策略综合技术报告 {snapshot.symbol} {snapshot.name or ''}".rstrip(),
        "",
        "## 基础信息",
        f"- 交易日：{snapshot.trade_date}",
        f"- 决策时间：{snapshot.decision_time}",
        f"- 数据截止：{snapshot.source_cutoff_time}",
        f"- 数据状态：{snapshot.data_quality.get('status')}",
        f"- 时间和数据提示：{list(snapshot.data_quality.get('warnings') or [])}",
    ]
    if holding_result is not None:
        lines.extend(_pipeline_section_lines("持仓策略", holding_result))
    lines.extend(_pipeline_section_lines("买入 / 加仓策略", buy_result))
    lines.extend(
        [
            "",
            "## 使用边界",
            "- 买入与持仓使用两条独立策略管线，本报告只是在展示层合并。",
            "- 当前没有未经校准的统一总分，原始分只在各策略内部有意义。",
            "- 本报告不是成交指令；仓位、资金、T+1 和交易限制仍需后续裁决。",
        ]
    )
    return "\n".join(lines) + "\n"


def _pipeline_section_lines(title: str, result: PipelineResult) -> list[str]:
    aggregation = result.aggregation
    labels = BUY_CONCLUSION_LABELS if result.snapshot.task_type == TaskType.BUY else HOLDING_CONCLUSION_LABELS
    lines = [
        "",
        f"## {title}",
        f"- 结论：{labels[aggregation.conclusion]} (`{aggregation.conclusion.value}`)",
        f"- 有效策略：{aggregation.effective_strategy_count}",
        f"- 支持 / 反对 / 中性 / 未知：{aggregation.support_count} / {aggregation.oppose_count} / {aggregation.neutral_count} / {aggregation.unknown_count}",
    ]
    for evaluation in result.run.evaluations:
        lines.extend(_evaluation_lines(evaluation))
    lines.append("")
    lines.append("### 策略家族与冲突")
    for family in aggregation.family_summary:
        lines.append(
            f"- {family.get('family')}：{family.get('opinion')}，策略 {list(family.get('strategies') or [])}"
        )
    if aggregation.conflicts:
        for conflict in aggregation.conflicts:
            lines.append(f"- 冲突：{conflict.get('message')} ({conflict.get('type')})")
    else:
        lines.append("- 未识别到有效策略之间的方向冲突。")
    lines.extend(
        [
            f"- 注册表版本：{result.run.registry_version}",
            f"- 汇总器版本：{aggregation.aggregator_version}",
            f"- 运行状态：{result.run.status.value}",
        ]
    )
    return lines


def write_analysis_session_report(
    path: Path,
    buy_result: PipelineResult,
    holding_result: PipelineResult | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_analysis_session_report(buy_result, holding_result),
        encoding="utf-8",
    )
    return path
