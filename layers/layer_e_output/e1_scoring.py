"""E1层：综合置信度打分——纯代码计算

评分公式（来自架构文档）：
    最终得分 = 100 - (C层扣分 + B+层扣分)

C层扣分：
    实际影响权重 = 基础影响权重 × (1 + MAD偏离度 / 3)
    期望影响 = Σ(归因概率 × 实际影响权重)
    C层扣分 = Σ(期望影响 × MAD偏离度)

B+层扣分：
    B+层扣分 = Σ(造假权重 × 严重度修正系数)
    其中造假权重 = -3, 行业性质权重 = 0

权重来源：config/weights.yaml
"""

import logging
from pathlib import Path
from typing import Optional

import yaml

from pipeline.context import PipelineContext
from schemas.report import ScoreBreakdown
from schemas.tags import HardTag

logger = logging.getLogger(__name__)

WEIGHTS_PATH = Path("config/weights.yaml")


# ────────────────────────────────────────────
# 权重加载
# ────────────────────────────────────────────

def _load_weights() -> dict:
    """加载权重配置"""
    if not WEIGHTS_PATH.exists():
        logger.warning(f"权重文件不存在: {WEIGHTS_PATH}，使用空配置")
        return {"weights": {}, "overrides": []}
    with open(WEIGHTS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _get_base_weight(indicator: str, hard_tags: Optional[list[HardTag]] = None) -> float:
    """获取某指标的 base_weight，考虑行业 overrides

    Args:
        indicator: 标准指标名
        hard_tags: 公司的硬标签列表（用于行业特殊规则）

    Returns:
        base_weight（已应用行业覆盖）
    """
    config = _load_weights()
    weights = config.get("weights", {})
    indicator_config = weights.get(indicator, {})

    if not indicator_config:
        logger.debug(f"指标 {indicator} 未在权重库中定义，使用默认值 -1")
        return -1.0  # 默认视为利空

    base = indicator_config.get("base_weight", -1.0)

    # 检查行业覆盖规则
    if hard_tags:
        overrides = config.get("overrides", [])
        for override in overrides:
            override_industries = override.get("industries", [])
            override_indicator = override.get("indicator", "")
            if override_indicator == indicator:
                for ht in hard_tags:
                    if ht.value in override_industries:
                        overridden = override.get("base_weight", base)
                        logger.debug(f"应用行业覆盖: {ht.value} → {indicator} 权重 {base}→{overridden}")
                        return overridden

    return base


def _get_amplification_cap(indicator: str) -> float:
    """获取放大系数上限"""
    config = _load_weights()
    weights = config.get("weights", {})
    indicator_config = weights.get(indicator, {})
    return indicator_config.get("amplification_cap", 3.0)


# ────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────

def run_scoring(ctx: PipelineContext) -> ScoreBreakdown:
    """计算最终置信度得分

    纯数学计算，不涉及 LLM，不依赖外部 API。
    """
    base = 100.0
    c_deduction = _calc_c_layer_deduction(ctx)
    bplus_deduction = _calc_bplus_layer_deduction(ctx)

    final = max(0.0, base - c_deduction - bplus_deduction)

    return ScoreBreakdown(
        base_score=base,
        c_layer_deduction=round(c_deduction, 2),
        bplus_layer_deduction=round(bplus_deduction, 2),
        final_score=round(final, 2),
    )


# ────────────────────────────────────────────
# C 层扣分计算
# ────────────────────────────────────────────

def _calc_c_layer_deduction(ctx: PipelineContext) -> float:
    """计算 C 层扣分

    公式：
    - 对每个偏差异常指标:
        实际影响权重 = base_weight × (1 + mad_multiple / 3)
        期望影响 = Σ(归因概率 × 实际影响权重)
        扣分 = 期望影响 × mad_multiple
    """
    if not ctx.deviations or not ctx.reasoning_results:
        return 0.0

    hard_tags = ctx.tags.hard_tags if ctx.tags else None
    deduction = 0.0

    # 将 reasoning_results 按 anomaly_indicator 建立索引
    reasoning_by_indicator: dict[str, dict] = {}
    for rr in ctx.reasoning_results:
        reasoning_by_indicator[rr.anomaly_indicator] = {
            "probabilities": rr.probabilities,
        }

    for dev in ctx.deviations:
        indicator = dev.indicator
        base_weight = _get_base_weight(indicator, hard_tags)
        cap = _get_amplification_cap(indicator)

        # 放大系数：偏离越大，权重越重（但有上限）
        amplification = 1 + dev.mad_multiple / 3
        amplification = min(amplification, cap)
        effective_weight = base_weight * amplification

        # 概率加权期望影响
        reasoning = reasoning_by_indicator.get(indicator, {})
        probabilities = reasoning.get("probabilities", {})

        if not probabilities:
            # 无归因时默认 -1 影响
            expected_impact = effective_weight
        else:
            expected_impact = 0.0
            for cause, prob in probabilities.items():
                # 行业性质=0权重，造假=-3，其他按 base_weight
                if "行业" in cause or "正常" in cause or "无风险" in cause:
                    cause_weight = 0.0
                elif "造假" in cause or "粉饰" in cause or "虚增" in cause:
                    cause_weight = -3.0
                else:
                    cause_weight = effective_weight
                expected_impact += prob * cause_weight

        # C层扣分 = 期望影响 × MAD偏离度
        item_deduction = expected_impact * dev.mad_multiple
        deduction += item_deduction

    # 扣分取绝对值（base_weight 为负时 deduction 为正）
    return abs(deduction)


# ────────────────────────────────────────────
# B+ 层扣分计算
# ────────────────────────────────────────────

def _calc_bplus_layer_deduction(ctx: PipelineContext) -> float:
    """计算 B+ 层扣分

    公式：
    - 造假权重默认 -3
    - 行业性质权重 = 0
    - 乘以严重度修正系数

    特殊处理：
    - 如果 D 层归因指向"行业性质"，扣分降为 0
    """
    if not ctx.logic_anomalies:
        return 0.0

    deduction = 0.0

    # 查找 D 层对 B+ 异常的归因
    bplus_reasoning: dict[str, dict] = {}
    if ctx.reasoning_results:
        for rr in ctx.reasoning_results:
            if rr.anomaly_source == "B+":
                bplus_reasoning[rr.anomaly_indicator] = {
                    "probabilities": rr.probabilities,
                }

    for anomaly in ctx.logic_anomalies:
        weight = -3.0  # 造假默认权重

        # 检查归因中是否有"行业性质"
        reasoning = bplus_reasoning.get(anomaly.check_name, {})
        probabilities = reasoning.get("probabilities", {})

        if probabilities:
            for cause, prob in probabilities.items():
                if "行业" in cause or "正常" in cause or "集团" in cause or "归集" in cause:
                    # 该异常归因于行业性质 → 权重降为 0
                    weight = 0.0
                    break

        # B+ 扣分 = 权重 × 严重度
        severity = anomaly.severity
        item_deduction = weight * severity
        deduction += item_deduction

    return abs(deduction)
