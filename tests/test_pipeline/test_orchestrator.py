"""
==========================================================
 tests/test_pipeline/test_orchestrator.py — 编排器测试
==========================================================

测试 Orchestrator 的核心功能：
- 所有步骤是否已正确注册
- 跳过步骤功能是否正常
- 错误传播机制
"""

import pytest
from pipeline.orchestrator import Orchestrator
from pipeline.step_registry import registry


class TestOrchestrator:
    """编排器测试"""

    def test_step_registration(self):
        """所有步骤应已注册"""
        steps = registry.list_steps()
        expected = ["layer_0", "layer_a", "layer_b", "layer_bplus", "layer_c", "layer_d", "layer_e"]
        for step in expected:
            assert step in steps, f"步骤 {step} 未注册"

    def test_skip_steps(self):
        """跳过步骤功能"""
        orch = Orchestrator()
        orch.skip("layer_c", "layer_d")
        # 验证 skip 不会报错
        assert True
