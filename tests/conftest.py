"""pytest 共享 fixtures"""

import json
import pytest
from pathlib import Path

from schemas.financial import FinancialStatement, FinancialField, ValidationResult
from schemas.benchmark import Benchmark


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_financials() -> FinancialStatement:
    """返回一份干净的测试用财务数据"""
    fixture_path = FIXTURES_DIR / "sample_financials.json"
    if fixture_path.exists():
        with open(fixture_path) as f:
            data = json.load(f)
        return FinancialStatement.model_validate(data)

    # 返回默认 mock 数据
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
        },
        income_statement={},
        cashflow={},
        validation=ValidationResult(is_valid=True, checks=[{"passed": True}]),
    )


@pytest.fixture
def mock_benchmark() -> Benchmark:
    """返回 mock 同行基准数据"""
    return Benchmark(
        peer_median={"存货周转率": 0.5, "毛利率": 0.4},
        historical_mean={"存货周转率": 0.45, "毛利率": 0.38},
        peer_companies=[],
    )
