"""B1层勾稽校验单元测试"""

import pytest
from layers.layer_b_extract.b1_validators import run_validation
from schemas.financial import FinancialStatement


class TestBalanceSheetEquation:
    """资产负债表恒等式测试"""

    def test_balanced_sheet_passes(self, sample_financials):
        """正常数据应通过校验"""
        result = run_validation(sample_financials)
        assert result.is_valid is True

    def test_unbalanced_sheet_fails(self):
        """故意不平衡的数据应失败"""
        # TODO: 构造不平衡的测试数据
        pass


class TestUnitConversion:
    """单位换算测试"""

    def test_wan_to_yuan(self):
        """万元→元的换算是否正确"""
        from layers.layer_b_extract.b1_extractors import _unit_to_multiplier
        assert _unit_to_multiplier("万元") == 10_000
        assert _unit_to_multiplier("元") == 1
        assert _unit_to_multiplier("亿元") == 100_000_000
