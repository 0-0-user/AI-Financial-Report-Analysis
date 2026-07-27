"""
==========================================================
 tests/test_layers/test_b1_validators.py — 勾稽校验测试
==========================================================

测试 B1 层勾稽校验逻辑：
- 正常财务数据应通过校验
- 故意不平衡的数据应检测失败
- 单位换算（万元→元、亿元→元）是否正确
"""

import pytest
from layers.layer_b_extract.b1_validators import run_validation
from schemas.financial import FinancialStatement, FinancialField, ValidationResult


class TestBalanceSheetEquation:
    """资产负债表恒等式测试"""

    def test_balanced_sheet_passes(self, sample_financials):
        """正常数据应通过校验"""
        result = run_validation(sample_financials)
        assert result.is_valid is True

    def test_unbalanced_sheet_fails(self):
        """故意不平衡的数据应失败"""
        # 构造不平衡数据：资产 1000 ≠ 负债 300 + 权益 400
        unbalanced = FinancialStatement(
            company_name="测试公司",
            stock_code="000000",
            year=2024,
            report_type="合并报表",
            balance_sheet={
                "Total_Assets": FinancialField(
                    standard_name="Total_Assets", raw_name="资产总计",
                    value=1000, original_unit="元", report_type="合并报表",
                ),
                "Total_Liabilities": FinancialField(
                    standard_name="Total_Liabilities", raw_name="负债合计",
                    value=300, original_unit="元", report_type="合并报表",
                ),
                "Total_Equity": FinancialField(
                    standard_name="Total_Equity", raw_name="所有者权益合计",
                    value=400, original_unit="元", report_type="合并报表",
                ),
            },
            income_statement={},
            cashflow={},
            validation=ValidationResult(is_valid=False, checks=[]),
        )
        result = run_validation(unbalanced)
        assert result.is_valid is False


class TestUnitConversion:
    """单位换算测试"""

    def test_wan_to_yuan(self):
        """万元→元的换算是否正确"""
        from layers.layer_b_extract.b1_extractors import _unit_to_multiplier
        assert _unit_to_multiplier("万元") == 10_000
        assert _unit_to_multiplier("元") == 1
        assert _unit_to_multiplier("亿元") == 100_000_000
        assert _unit_to_multiplier("千元") == 1_000
        assert _unit_to_multiplier("unknown") == 1  # 未知单位默认 1
