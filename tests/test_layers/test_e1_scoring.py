"""
==========================================================
 tests/test_layers/test_e1_scoring.py — 评分逻辑测试
==========================================================

测试 E1 层置信度打分逻辑：
- 无异常时应保持 100 分
- 有异常时应适当扣分
- 得分不应低于 0（防止极端异常导致负分）

测试方式：构造 PipelineContext 并注入 mock 的异常/偏差数据，
然后调用 run_scoring 验证打分结果。
"""

import pytest
from pipeline.context import PipelineContext
from schemas.anomaly import DeviationAnomaly, LogicAnomaly
from schemas.reasoning import ProbabilityAssignment, Explanation, Hypothesis
from schemas.report import ScoreBreakdown
from layers.layer_e_output.e1_scoring import run_scoring


class TestScoring:
    """置信度打分测试"""

    def test_base_score_no_anomalies(self):
        """无异常时应保持 100 分"""
        ctx = PipelineContext()
        # 不注入任何异常/偏差，所有扣分项应为 0
        result = run_scoring(ctx)

        assert isinstance(result, ScoreBreakdown)
        assert result.base_score == 100
        assert result.c_layer_deduction == 0
        assert result.bplus_layer_deduction == 0
        assert result.final_score == 100

    def test_deduction_with_c_layer_deviation(self):
        """C 层偏差异常应正确扣分"""
        ctx = PipelineContext()
        ctx.deviations = [
            DeviationAnomaly(
                indicator="存货周转率",
                actual_value=0.1,
                benchmark_value=0.5,
                mad_multiple=4.0,  # 大幅偏离
                severity="extreme",
            ),
            DeviationAnomaly(
                indicator="毛利率",
                actual_value=0.6,
                benchmark_value=0.4,
                mad_multiple=2.5,  # 一般偏离
                severity="abnormal",
            ),
        ]
        ctx.reasoning_results = [
            ProbabilityAssignment(
                anomaly_indicator="存货周转率",
                anomaly_source="C",
                lookups=[Explanation(summary="行业下行", source_text="因行业需求下降", page_number=10)],
                hypotheses=[Hypothesis(hypothesis="产品滞销", reasoning="...", source="行业数据")],
                probabilities={"经营滞销": 0.6, "战略备货": 0.3, "其他": 0.1},
            ),
            ProbabilityAssignment(
                anomaly_indicator="毛利率",
                anomaly_source="C",
                lookups=[],
                hypotheses=[Hypothesis(hypothesis="成本上升", reasoning="...", source="行业数据")],
                probabilities={"成本推动": 0.7, "其他": 0.3},
            ),
        ]
        result = run_scoring(ctx)

        assert result.c_layer_deduction > 0
        assert result.final_score < 100
        assert result.base_score == 100

    def test_deduction_with_bplus_anomaly(self):
        """B+ 层逻辑异常应正确扣分"""
        ctx = PipelineContext()
        ctx.logic_anomalies = [
            LogicAnomaly(
                check_name="净现比",
                value=0.3,
                threshold=0.6,
                severity=3.0,  # 严重度 3 分
                summary="利润含金量低",
            ),
            LogicAnomaly(
                check_name="存贷双高",
                value=0.4,
                threshold=0.3,
                severity=4.0,
                summary="存贷双高可疑",
            ),
        ]
        result = run_scoring(ctx)

        assert result.bplus_layer_deduction > 0
        assert result.final_score < 100
        assert result.final_score >= 0

    def test_score_never_negative(self):
        """极端异常受 amplification_cap 保护，得分不会过低"""
        ctx = PipelineContext()
        ctx.deviations = [
            DeviationAnomaly(
                indicator="存货周转率",
                actual_value=0.01,
                benchmark_value=0.5,
                mad_multiple=20.0,  # 极端偏离
                severity="extreme",
            ),
        ]
        ctx.reasoning_results = [
            ProbabilityAssignment(
                anomaly_indicator="存货周转率",
                anomaly_source="C",
                lookups=[],
                hypotheses=[Hypothesis(hypothesis="极端异常", reasoning="...", source="数据")],
                probabilities={"造假": 0.9, "其他": 0.1},
            ),
        ]
        result = run_scoring(ctx)

        # amplification_cap=3.0 限制，扣分不会超过 60
        assert result.final_score >= 0
        assert result.c_layer_deduction == 60.0
        assert result.final_score == 40.0
