"""
==========================================================
 tests/conftest.py — 共享测试夹具
==========================================================

pytest 的共享 fixtures 定义文件。

提供各层测试通用的 mock 数据: 
- sample_financials:  一份干净的测试用财务数据 (直接从JSON加载或返回默认mock) 
- mock_benchmark:     包含同行中位数和历史均值的基准 mock

fixtures 目录 (tests/fixtures/) 中包含更完整的测试数据文件。
"""

import json
import pytest
from pathlib import Path

from schemas.financial import FinancialStatement, FinancialField, ValidationResult, ValidationCheck
from schemas.benchmark import IndustryProfile, Benchmark


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_financials() -> FinancialStatement:
    """返回一份干净的测试用财务数据"""
    fixture_path = FIXTURES_DIR / "sample_financials.json"
    if fixture_path.exists():
        with open(fixture_path, encoding="utf-8") as f:
            data = json.load(f)
        return FinancialStatement.model_validate(data)

    # 返回默认 mock 数据 (含三大报表字段) 
    return FinancialStatement(
        company_name="测试公司",
        stock_code="000000",
        year=2024,
        report_type="合并报表",
        balance_sheet={
            "Total_Assets": FinancialField(
                standard_name="Total_Assets", raw_name="资产总计",
                value=100_000_000, original_unit="元", report_type="合并报表",
            ),
            "Total_Liabilities": FinancialField(
                standard_name="Total_Liabilities", raw_name="负债合计",
                value=60_000_000, original_unit="元", report_type="合并报表",
            ),
            "Total_Equity": FinancialField(
                standard_name="Total_Equity", raw_name="所有者权益合计",
                value=40_000_000, original_unit="元", report_type="合并报表",
            ),
        },
        income_statement={},
        cashflow={},
        validation=ValidationResult(is_valid=True, checks=[ValidationCheck(check_name="test", passed=True, detail="")]),
    )


@pytest.fixture
def mock_benchmark() -> Benchmark:
    """返回 mock 同行基准数据"""
    return Benchmark(
        industry=IndustryProfile(industry_name="测试行业"),
        peer_median={"存货周转率": 0.5, "毛利率": 0.4},
        historical_mean={"存货周转率": 0.45, "毛利率": 0.38},
        peer_companies=[],
    )
