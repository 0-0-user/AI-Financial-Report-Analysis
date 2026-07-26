"""B1层：代码准确提取表格数据——纯 Pandas 定位取数"""

import pandas as pd
from schemas.financial import FinancialStatement, FinancialField, ValidationResult


def run_extraction(raw_doc, guide: dict) -> FinancialStatement:
    """纯代码：根据 B0 指引定位表格行列，提取数字并换算单位

    流程：
    1. 根据 guide.field_mapping 定位每个字段的行列坐标
    2. 从原始表格数据中抓取对应数值
    3. 统一换算为"元"（原始单位 × 换算系数）
    4. 组装为 FinancialStatement
    """
    # TODO: 实现 Pandas 表格提取逻辑
    return FinancialStatement(
        company_name="",
        stock_code="",
        year=0,
        report_type=guide.get("report_type", ""),
        balance_sheet={},
        income_statement={},
        cashflow={},
        validation=ValidationResult(is_valid=False, checks=[]),
    )


def _unit_to_multiplier(unit: str) -> float:
    """单位字符串转数字乘数"""
    mapping = {"元": 1, "千元": 1000, "万元": 10_000, "亿元": 100_000_000}
    return mapping.get(unit, 1)
