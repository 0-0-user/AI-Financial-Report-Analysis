"""E1层：基于 D-S 融合概率的「语义冲突调节评分系统」

评分公式（新）：
    Score_j = W_phe_j × (1 - K_j) × Σ_i P_j(H_i) × S_base(H_i)

    C层偏差: W_phe = min(mad_multiple / 3, 5.0)
    B+异常:  W_phe = severity（直接用，范围0.5-5.0）

    Total_deduction = Σ_j Score_j
    Final = 100 + Total_deduction   # 允许负数

数据来源：
    - W_phe: B+层 severity / C层 mad_multiple
    - K:     D2 层 D-S 证据理论的冲突系数
    - P(H):  D2 层 pignistic 概率分布
    - S_base: D3 层语义关键词匹配结果
"""

import logging
from pathlib import Path
from typing import Optional

import yaml

from pipeline.context import PipelineContext
from schemas.report import ScoreBreakdown, AnomalyScoreDetail, CauseDetail
from schemas.tags import HardTag
from schemas.anomaly import LogicAnomaly, DeviationAnomaly

logger = logging.getLogger(__name__)

WEIGHTS_PATH = Path("config/weights.yaml")


# ────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────

def run_scoring(ctx: PipelineContext) -> ScoreBreakdown:
    """计算最终置信度得分（新公式：语义冲突调节评分）

    流程：
    1. 遍历 B+ 层和 C 层所有异常
    2. 对每个异常，匹配 D 层推理结果
    3. 计算单异常扣分 Score_j
    4. 汇总得到最终得分
    """
    anomaly_details: list[AnomalyScoreDetail] = []

    # 处理 B+ 层异常
    if ctx.logic_anomalies:
        for anomaly in ctx.logic_anomalies:
            detail = _score_one_anomaly(
                indicator=anomaly.check_name,
                source="B+",
                w_phe=anomaly.severity,
                reasoning_results=ctx.reasoning_results or [],
            )
            if detail is not None:
                anomaly_details.append(detail)

    # 处理 C 层偏差异常
    if ctx.deviations:
        for dev in ctx.deviations:
            w_phe = _calc_c_w_phe(dev.mad_multiple)
            detail = _score_one_anomaly(
                indicator=dev.indicator,
                source="C",
                w_phe=w_phe,
                reasoning_results=ctx.reasoning_results or [],
            )
            if detail is not None:
                anomaly_details.append(detail)

    total_deduction = sum(d.anomaly_score for d in anomaly_details)
    final_score = 100.0 + total_deduction  # 允许负数

    return ScoreBreakdown(
        base_score=100.0,
        anomaly_details=anomaly_details,
        total_deduction=round(total_deduction, 2),
        final_score=round(final_score, 2),
    )


# ────────────────────────────────────────────
# 单异常评分
# ────────────────────────────────────────────

def _score_one_anomaly(
    indicator: str,
    source: str,
    w_phe: float,
    reasoning_results: list,
) -> Optional[AnomalyScoreDetail]:
    """计算单个异常的扣分

    公式：Score_j = W_phe × (1 - K) × Σ P × S_base
    """
    # 查找匹配的 D 层推理结果
    reasoning = _find_reasoning(reasoning_results, indicator, source)
    if reasoning is None:
        return None

    # K 值（D-S 冲突系数）
    ds_meta = reasoning.ds_metadata or {}
    k_value = ds_meta.get("conflict_K", 0.0)
    delta = 1.0 - k_value

    # 从 mass_final 获取 m(Θ)（未分配的不确定性）
    mass_final = ds_meta.get("mass_final", {})
    m_theta = mass_final.get("Θ", 0.0)

    # 各归因原因的影响分
    probabilities = reasoning.probabilities or {}
    semantic_scores = reasoning.semantic_scores or {}
    semantic_keywords = reasoning.semantic_keywords or {}

    causes_detail: list[CauseDetail] = []
    weighted_sum = 0.0
    # 用于信任区间的累计：Σ m_i × S_base(H_i)
    mass_weighted_raw = 0.0

    for cause, prob in probabilities.items():
        # S_base: D3 语义匹配结果，没有则默认 -1
        score_base = semantic_scores.get(cause, -1)
        matched_kws = semantic_keywords.get(cause, [])

        # 有效影响值 = S_base × (1 - K)
        effective_score = score_base * delta
        weighted_sum += prob * effective_score

        # 从 mass_final 获取该 cause 的原始 mass
        cause_mass = mass_final.get(cause, 0.0)
        mass_weighted_raw += cause_mass * score_base

        causes_detail.append(CauseDetail(
            cause=cause,
            probability=round(prob, 4),
            score_base=score_base,
            matched_keywords=matched_kws,
            effective_score=round(score_base * delta, 4),
        ))

    # 单异常扣分 = W_phe × Σ(P × S_base × (1-K))
    anomaly_score = w_phe * weighted_sum

    # ── 计算信任区间 [Bel, Pl] ──
    known = 1.0 - m_theta
    if m_theta > 0 and semantic_scores and known > 0:
        # 使用全局语义评分范围的极值 [-3, +2]
        GLOBAL_S_MIN = -3
        GLOBAL_S_MAX = 2

        # best: m(Θ) 分配给最高分 → 扣分最少 → 最乐观
        best_weighted = (mass_weighted_raw + m_theta * GLOBAL_S_MAX) / known
        score_best = w_phe * delta * best_weighted

        # worst: m(Θ) 分配给最低分 → 扣分最多 → 最悲观
        worst_weighted = (mass_weighted_raw + m_theta * GLOBAL_S_MIN) / known
        score_worst = w_phe * delta * worst_weighted
    else:
        score_best = anomaly_score
        score_worst = anomaly_score

    return AnomalyScoreDetail(
        indicator=indicator,
        source=source,
        w_phe=round(w_phe, 4),
        k_value=round(k_value, 4),
        delta=round(delta, 4),
        causes=causes_detail,
        anomaly_score=round(anomaly_score, 4),
        score_bel=round(score_worst, 4),
        score_pl=round(score_best, 4),
    )


# ────────────────────────────────────────────
# C 层 W_phe 映射
# ────────────────────────────────────────────

def _calc_c_w_phe(mad_multiple: float) -> float:
    """C 层偏差的严重性系数映射

    W_phe = min(mad_multiple / 3, 5.0)

    MAD=2.0  →  W_phe=0.67  (轻微异常)
    MAD=5.0  →  W_phe=1.67  (一般异常)
    MAD=10.0 →  W_phe=3.33  (严重异常)
    MAD=15.0 →  W_phe=5.0   (极端异常，cap)
    """
    return min(mad_multiple / 3.0, 5.0)


# ────────────────────────────────────────────
# 匹配 D 层推理结果
# ────────────────────────────────────────────

def _find_reasoning(
    reasoning_results: list,
    indicator: str,
    source: str,
) -> Optional:
    """在 D 层推理结果中查找匹配的异常

    匹配规则：
    - 对 B+ 层: check_name == anomaly_indicator
    - 对 C 层:  indicator == anomaly_indicator
    """
    for rr in reasoning_results:
        if rr.anomaly_indicator == indicator and rr.anomaly_source == source:
            return rr
    # 无匹配：D 层可能没有对该异常进行推理
    # 按"失败即终止"原则，理论上不应出现
    logger.warning(f"未找到 D 层推理结果: indicator={indicator}, source={source}")
    return None
