"""E1层：综合置信度打分——纯代码计算"""

from typing import Optional
from pipeline.context import PipelineContext
from schemas.report import ScoreBreakdown
from layers.layer_bplus_internal.logic_checks import calc_severity


def run_scoring(ctx: PipelineContext) -> ScoreBreakdown:
    """计算最终置信度得分

    公式：
    最终得分 = 100 - (C层扣分 + B+层扣分)

    C层扣分 = Σ(期望影响 × MAD偏离度)
      期望影响 = Σ(概率 × 实际影响权重)
      实际影响权重 = 基础影响权重 × (1 + MAD偏离度/3)

    B+层扣分 = Σ(权重 × 修正系数)
      造假权重 = -3, 行业性质权重 = 0
    """
    base = 100.0
    c_deduction = _calc_c_layer_deduction(ctx)
    bplus_deduction = _calc_bplus_layer_deduction(ctx)

    final = max(0.0, base - c_deduction - bplus_deduction)

    return ScoreBreakdown(
        base_score=base,
        c_layer_deduction=c_deduction,
        bplus_layer_deduction=bplus_deduction,
        final_score=round(final, 2),
    )


def _calc_c_layer_deduction(ctx: PipelineContext) -> float:
    """计算 C 层扣分"""
    if not ctx.deviations or not ctx.reasoning_results:
        return 0.0

    # TODO: 从 config/weights.yaml 加载权重
    deduction = 0.0

    for dev, prob in zip(ctx.deviations, ctx.reasoning_results):
        base_weight = -1.0  # 默认利空权重
        effective_weight = base_weight * (1 + dev.mad_multiple / 3)

        # 概率加权求和（所有 p 加起来 = 1.0）
        expected_impact = 0.0
        for cause, p in prob.probabilities.items():
            expected_impact += p * effective_weight

        deduction += expected_impact * dev.mad_multiple

    return abs(deduction)


def _calc_bplus_layer_deduction(ctx: PipelineContext) -> float:
    """计算 B+ 层扣分"""
    if not ctx.logic_anomalies:
        return 0.0

    deduction = 0.0
    for anomaly in ctx.logic_anomalies:
        # 造假权重 -3
        severity = anomaly.severity
        deduction += -3 * severity

    return abs(deduction)
