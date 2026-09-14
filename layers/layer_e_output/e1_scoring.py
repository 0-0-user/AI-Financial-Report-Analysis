"""E1层: 基于 D-S 融合概率的「语义冲突调节评分系统」

评分公式 (新) : 
    Score_j = W_phe_j x (1 - K_j) x Σ_i P_j(H_i) x S_base(H_i)

    C层偏差: W_phe = min(mad_multiple / 3, 5.0)
    B+异常:  W_phe = severity (直接用，范围0.5-5.0) 

    Score_j <= 0                    # 异常只能扣分或中性，见 _clamp_non_positive
    Total_deduction = Σ_j Score_j
    Final = 100 + Total_deduction   # 允许负数（下界不钳）

数据来源: 
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

# D2 的 _pignistic_transform 把未知质量 Θ 记名成这个桶。它表示"未能归因"，
# 不是一条语义可评的归因 —— 详见 _score_one_anomaly 里的说明。
OTHER_CAUSE = "其他原因"


# ────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────

def run_scoring(ctx: PipelineContext) -> ScoreBreakdown:
    """计算最终置信度得分 (新公式: 语义冲突调节评分) 

    流程: 
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
    final_score = 100.0 + total_deduction  # 允许负数（下界不钳，上界由 Score_j <= 0 保证）

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

    公式: Score_j = W_phe x (1 - K) x Σ P x S_base
    """
    # 查找匹配的 D 层推理结果
    reasoning = _find_reasoning(reasoning_results, indicator, source)
    if reasoning is None:
        return None

    # K 值 (D-S 冲突系数) 
    ds_meta = reasoning.ds_metadata or {}
    k_value = ds_meta.get("conflict_K", 0.0)
    delta = 1.0 - k_value

    # 从 mass_final 获取 m(Θ) (未分配的不确定性) 
    mass_final = ds_meta.get("mass_final", {})

    # 各归因原因的影响分
    probabilities = reasoning.probabilities or {}
    semantic_scores = reasoning.semantic_scores or {}
    semantic_keywords = reasoning.semantic_keywords or {}

    causes_detail: list[CauseDetail] = []
    weighted_sum = 0.0
    # 用于信任区间的累计: Σ m_i x S_base(H_i)
    mass_weighted_raw = 0.0

    # 「其他原因」是 D2 把未知质量 Θ 记名后的桶（见 d2_probability._pignistic_transform:
    # mass_final["Θ"] -> probabilities["其他原因"]，是同一份质量的两种记名）。
    # 语义上等于"未能归因"，不是一条可评分的归因。D3 给它语义分 0（中性），
    # 若按普通归因处理，Σ(P x S_base) 恒为 0 -> 扣 0 分 -> 满分，
    # 即"无法归因"被当成"没有问题"（fail-open），与 D2 注释
    #「避免把不确定性伪装成确定性归因」自相矛盾。
    # 故把它作为 m(Θ)：不参与点估计，只参与信任区间。
    #
    # 取值只允许来自 probabilities 一处。曾同时读 mass_final["Θ"] 再相加，
    # 那是对同一份质量重复计数 —— 实测苏美达「净资产收益率」:
    # mass_final["Θ"]=0.4978 且 probabilities["其他原因"]=0.4978，
    # 相加得 m(Θ)=0.9956（真实值 0.4978），known 被压到 0.0044，区间随之发散。
    # probabilities 由 _pignistic_transform 归一化 (Σ=1)，故
    # m(Θ)=P(其他原因) 与 Σ(已知 P)=1-m(Θ) 恒自洽。
    m_theta = probabilities.get(OTHER_CAUSE, 0.0)
    if not probabilities:
        # 兜底: D2 正常必产出 pignistic 概率；若缺失则退回原始 mass 取 Θ，
        # 免得"没有概率分布"被误当成"没有不确定性"（那正是本函数要堵的 fail-open）。
        m_theta = mass_final.get("Θ", 0.0)

    for cause, prob in probabilities.items():
        if cause == OTHER_CAUSE:
            continue
        # S_base: D3 语义匹配结果，没有则默认 -1
        score_base = semantic_scores.get(cause, -1)
        matched_kws = semantic_keywords.get(cause, [])

        # 有效影响值 = S_base x (1 - K)
        effective_score = score_base * delta
        weighted_sum += prob * effective_score

        # 累计 Σ P_i x S_i，供信任区间使用。
        # 用 pignistic 概率而非原始 mass: 已知归因二者本相等（见 _pignistic_transform），
        # 但 probabilities 已归一化，能保证下面"总质量 = 1"的不变式严格成立。
        mass_weighted_raw += prob * score_base

        causes_detail.append(CauseDetail(
            cause=cause,
            probability=round(prob, 4),
            score_base=score_base,
            matched_keywords=matched_kws,
            effective_score=round(score_base * delta, 4),
        ))

    # 单异常扣分 = W_phe x Σ(P x S_base x (1-K))
    #
    # 上限钳到 0：异常只能扣分或中性，不能加分。
    # semantic_scoring.yaml 的 +1/+2 正向档语义是「这条归因是良性的」-> 少扣分；
    # 但 Σ(P x S_base) 是**带符号**加权和，良性归因占多数时会把它推成正数，
    # 于是「检出一个异常」反倒给总分加分。实测苏美达 2026-09-11：
    #   销售净利率 +0.3473、净资产收益率 +0.5945（该条 w_phe 已打满 5.0），
    #   把真正扣分的资产负债率 -1.2471 抵成合计 -0.3053 -> 99.69 分，
    #   且信任区间上界 105.11 超出了满分。
    # 检出异常却让公司高于满分，语义上说不通：「良性归因」的极限是
    # 「本条不扣分」，不是「倒找钱」。
    anomaly_score = _clamp_non_positive(w_phe * weighted_sum)

    # ── 计算信任区间 [Bel, Pl] ──
    # 把 m(Θ) 整块改派给最高/最低语义分，得最乐观 / 最悲观的期望。
    # mass_weighted_raw = Σ P_i x S_i，加上 m(Θ) x s 后总质量恰为 1，
    # 本身就是一个期望值 —— 不得再除以 known(=1-m(Θ))。
    # 曾除以 known: 那会把区间放大 1/known 倍，且 m(Θ) -> 1 时发散 ——
    # 实测「净资产收益率」known=0.0044，产出 [-3091.25, +2565.57]，
    # 在 0-100 分制下是不可能出现的数。
    # 去掉除法后 known==0 的极端情形也自然成立: 此时 Σ P_i = 0，
    # best 退化为 GLOBAL_S_MAX、worst 退化为 GLOBAL_S_MIN，无需特判。
    if m_theta > 0:
        GLOBAL_S_MIN = -3
        GLOBAL_S_MAX = 2
        best_weighted = mass_weighted_raw + m_theta * GLOBAL_S_MAX
        worst_weighted = mass_weighted_raw + m_theta * GLOBAL_S_MIN
        # 区间端点同样钳位：乐观端不钳的话，上界会重新超过 100
        # （实测 105.11 = 100 + 1.1987 + 0.3206 + 3.5945），
        # 等于把点估计里堵掉的加分从区间里漏回来。
        score_best = _clamp_non_positive(w_phe * delta * best_weighted)
        score_worst = _clamp_non_positive(w_phe * delta * worst_weighted)
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
        m_theta=round(m_theta, 4),
    )


# ────────────────────────────────────────────
# C 层 W_phe 映射
# ────────────────────────────────────────────

def _calc_c_w_phe(mad_multiple: float) -> float:
    """C 层偏差的严重性系数映射

    W_phe = min(mad_multiple / 3, 5.0)

    MAD=2.0  ->  W_phe=0.67  (轻微异常)
    MAD=5.0  ->  W_phe=1.67  (一般异常)
    MAD=10.0 ->  W_phe=3.33  (严重异常)
    MAD=15.0 ->  W_phe=5.0   (极端异常，cap)
    """
    return min(mad_multiple / 3.0, 5.0)


# ────────────────────────────────────────────
# 单异常贡献的上界
# ────────────────────────────────────────────

def _clamp_non_positive(value: float) -> float:
    """异常对总分的贡献上限为 0：检出异常只能扣分或中性，不能加分。

    Score_j 的公式是 W_phe x (1-K) x Σ P x S_base，而 S_base 可正可负
    （见 config/semantic_scoring.yaml 的 +1/+2 档），所以原始值没有天然的符号。
    本函数把这个符号钉死 —— 它是 `Final = 100 + Σ Score_j` 这个式子能读出
    「扣分」二字的必要条件（具体案例见 _score_one_anomaly 内的注释）。
    """
    return min(0.0, value)


# ────────────────────────────────────────────
# 匹配 D 层推理结果
# ────────────────────────────────────────────

def _find_reasoning(
    reasoning_results: list,
    indicator: str,
    source: str,
) -> Optional:
    """在 D 层推理结果中查找匹配的异常

    匹配规则: 
    - 对 B+ 层: check_name == anomaly_indicator
    - 对 C 层:  indicator == anomaly_indicator
    """
    for rr in reasoning_results:
        if rr.anomaly_indicator == indicator and rr.anomaly_source == source:
            return rr
    # 无匹配: D 层可能没有对该异常进行推理
    # 按"失败即终止"原则，理论上不应出现
    logger.warning(f"未找到 D 层推理结果: indicator={indicator}, source={source}")
    return None
