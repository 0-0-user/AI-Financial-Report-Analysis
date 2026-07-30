"""
==========================================================
 tests/test_layers/test_e1_scoring.py — 新评分公式测试
==========================================================

测试 E1 层新公式：
    Score_j = W_phe × (1 - K) × Σ P × S_base
    Final = 100 + Σ Score_j  （允许负数，无clamp）

测试方式：构造 PipelineContext 注入 mock 数据，验证各项中间值。
"""

import pytest
from pipeline.context import PipelineContext
from schemas.anomaly import DeviationAnomaly, LogicAnomaly
from schemas.reasoning import ProbabilityAssignment, Explanation, Hypothesis
from schemas.report import ScoreBreakdown, AnomalyScoreDetail
from layers.layer_e_output.e1_scoring import run_scoring, _calc_c_w_phe


class TestScoring:
    """新公式的置信度打分测试"""

    def test_base_score_no_anomalies(self):
        """无异常时应保持 100 分"""
        ctx = PipelineContext()
        result = run_scoring(ctx)

        assert isinstance(result, ScoreBreakdown)
        assert result.base_score == 100
        assert result.total_deduction == 0
        assert result.final_score == 100
        assert result.anomaly_details == []

    def test_deduction_with_c_layer_deviation(self):
        """C 层偏差异常 + D层推理 + D3语义匹配 → 正确扣分"""
        ctx = PipelineContext()
        ctx.deviations = [
            DeviationAnomaly(
                indicator="存货周转率",
                actual_value=0.1,
                benchmark_value=0.5,
                mad_multiple=4.0,  # W_phe = min(4/3, 5) = 1.33
                severity="extreme",
            ),
        ]
        ctx.reasoning_results = [
            ProbabilityAssignment(
                anomaly_indicator="存货周转率",
                anomaly_source="C",
                lookups=[],
                hypotheses=[],
                probabilities={"行业需求下降导致销售放缓": 0.6, "产品滞销导致库存积压": 0.4},
                ds_metadata={"conflict_K": 0.2},
                semantic_scores={"行业需求下降导致销售放缓": -1, "产品滞销导致库存积压": -2},
                semantic_keywords={"行业需求下降导致销售放缓": ["下滑"], "产品滞销导致库存积压": ["异常", "风险"]},
            ),
        ]
        result = run_scoring(ctx)

        # 验证计算结果
        assert len(result.anomaly_details) == 1
        detail = result.anomaly_details[0]

        # W_phe = min(4/3, 5) ≈ 1.3333
        assert detail.w_phe == pytest.approx(1.3333, rel=1e-3)
        # K = 0.2
        assert detail.k_value == 0.2
        assert detail.delta == 0.8

        # weighted_sum = 0.6 × (-1) + 0.4 × (-2) = -0.6 - 0.8 = -1.4
        # effective = -1.4 × 0.8 = -1.12
        # anomaly_score = 1.3333 × (-1.12) ≈ -1.4933 → round到2位 = -1.49
        assert detail.anomaly_score == pytest.approx(-1.49, rel=1e-2)

        assert result.total_deduction == pytest.approx(-1.49, rel=1e-2)
        assert result.final_score == pytest.approx(98.51, rel=1e-2)

    def test_bplus_anomaly_deduction(self):
        """B+ 层逻辑异常 + D层推理 → 正确扣分"""
        ctx = PipelineContext()
        ctx.logic_anomalies = [
            LogicAnomaly(
                check_name="利润含金量异常",
                value=0.3,
                threshold=0.6,
                severity=3.0,  # 直接用做 W_phe
                summary="净现比低",
            ),
        ]
        ctx.reasoning_results = [
            ProbabilityAssignment(
                anomaly_indicator="利润含金量异常",
                anomaly_source="B+",
                lookups=[],
                hypotheses=[],
                probabilities={"行业竞争加剧导致回款困难": 0.7, "企业放宽信用政策虚增收入": 0.3},
                ds_metadata={"conflict_K": 0.35},
                semantic_scores={"行业竞争加剧导致回款困难": 0, "企业放宽信用政策虚增收入": -3},
                semantic_keywords={"行业竞争加剧导致回款困难": ["行业性质"], "企业放宽信用政策虚增收入": ["虚增"]},
            ),
        ]
        result = run_scoring(ctx)

        assert len(result.anomaly_details) == 1
        detail = result.anomaly_details[0]

        assert detail.source == "B+"
        assert detail.w_phe == 3.0
        assert detail.k_value == 0.35
        assert detail.delta == 0.65

        # weighted_sum = 0.7 × 0 + 0.3 × (-3) = -0.9
        # effective = -0.9 × 0.65 = -0.585
        # anomaly_score = 3.0 × (-0.585) = -1.755
        assert detail.anomaly_score == pytest.approx(-1.755, rel=1e-3)
        assert result.final_score == pytest.approx(98.245, rel=1e-3)

    def test_k_zero_equals_no_conflict(self):
        """K=0 时 δ=1，全额生效"""
        ctx = PipelineContext()
        ctx.deviations = [
            DeviationAnomaly(
                indicator="毛利率", actual_value=0.2, benchmark_value=0.5,
                mad_multiple=3.0, severity="abnormal",
            ),
        ]
        ctx.reasoning_results = [
            ProbabilityAssignment(
                anomaly_indicator="毛利率",
                anomaly_source="C",
                lookups=[], hypotheses=[],
                probabilities={"成本上升压缩利润空间": 1.0},
                ds_metadata={"conflict_K": 0.0},
                semantic_scores={"成本上升压缩利润空间": -1},
                semantic_keywords={},
            ),
        ]
        result = run_scoring(ctx)

        detail = result.anomaly_details[0]
        assert detail.delta == 1.0  # 不打折
        # W_phe = min(3/3, 5) = 1.0
        assert detail.w_phe == 1.0
        # anomaly_score = 1.0 × 1.0 × (1.0 × -1) = -1.0
        assert detail.anomaly_score == -1.0

    def test_negative_score_allowed(self):
        """允许负分，不 clamp"""
        ctx = PipelineContext()
        ctx.logic_anomalies = [
            LogicAnomaly(
                check_name="财务造假",
                value=999.0, threshold=1.0,
                severity=5.0,  # 最高严重度
                summary="系统性财务造假",
            ),
        ]
        ctx.reasoning_results = [
            ProbabilityAssignment(
                anomaly_indicator="财务造假",
                anomaly_source="B+",
                lookups=[], hypotheses=[],
                probabilities={"系统性财务造假虚增收入": 1.0},
                ds_metadata={"conflict_K": 0.0},
                semantic_scores={"系统性财务造假虚增收入": -3},
                semantic_keywords={"系统性财务造假虚增收入": ["造假"]},
            ),
        ]
        result = run_scoring(ctx)

        # anomaly_score = 5.0 × 1.0 × (1.0 × -3) = -15.0
        assert result.total_deduction == -15.0
        assert result.final_score == 85.0  # 100 - 15 = 85

        # 极端：7 个同样的极端异常
        ctx2 = PipelineContext()
        ctx2.logic_anomalies = [LogicAnomaly(
            check_name=f"异常{i}", value=1.0, threshold=0.5,
            severity=5.0, summary=f"异常{i}",
        ) for i in range(7)]
        ctx2.reasoning_results = [
            ProbabilityAssignment(
                anomaly_indicator=f"异常{i}",
                anomaly_source="B+",
                lookups=[], hypotheses=[],
                probabilities={"严重问题": 1.0},
                ds_metadata={"conflict_K": 0.0},
                semantic_scores={"严重问题": -3},
                semantic_keywords={},
            ) for i in range(7)
        ]
        result2 = run_scoring(ctx2)

        # 7 × (-15) = -105, final = 100 - 105 = -5
        assert result2.final_score == -5.0  # 允许负分

    def test_w_phe_c_mapping(self):
        """C 层 W_phe 映射公式验证"""
        assert _calc_c_w_phe(2.0) == pytest.approx(0.6667, rel=1e-3)  # 轻微
        assert _calc_c_w_phe(5.0) == pytest.approx(1.6667, rel=1e-3)  # 一般
        assert _calc_c_w_phe(10.0) == pytest.approx(3.3333, rel=1e-3)  # 严重
        assert _calc_c_w_phe(15.0) == 5.0  # cap
        assert _calc_c_w_phe(100.0) == 5.0  # 极端值 cap

    def test_no_reasoning_match_returns_none(self):
        """异常无匹配的 D 层推理结果时，跳过该异常"""
        ctx = PipelineContext()
        ctx.deviations = [
            DeviationAnomaly(
                indicator="存货周转率", actual_value=0.1, benchmark_value=0.5,
                mad_multiple=4.0, severity="extreme",
            ),
        ]
        # 没有注入 reasoning_results
        result = run_scoring(ctx)
        assert result.anomaly_details == []  # 无匹配，跳过
        assert result.final_score == 100.0  # 保留基础分
