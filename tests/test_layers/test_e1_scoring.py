"""E1层评分逻辑单元测试"""

import pytest
from schemas.report import ScoreBreakdown
from layers.layer_e_output.e1_scoring import run_scoring


class TestScoring:
    """置信度打分测试"""

    def test_base_score_no_anomalies(self):
        """无异常时应保持 100 分"""
        # TODO: 构造无异常的 PipelineContext
        pass

    def test_deduction_with_anomaly(self):
        """有异常时应适当扣分"""
        # TODO: 构造带异常的 PipelineContext
        pass

    def test_score_never_negative(self):
        """得分不应低于 0"""
        # TODO: 构造极端异常场景
        pass
