from pydantic import BaseModel
from typing import Optional


class FinancialField(BaseModel):
    """统一财务字段"""
    standard_name: str
    raw_name: str
    value: float
    original_unit: str
    report_type: str


class ValidationResult(BaseModel):
    """勾稽校验结果"""
    is_valid: bool
    checks: list[dict]  # [{check_name, passed, detail}]
    error_message: Optional[str] = None


class FinancialStatement(BaseModel):
    """一份完整的财务数据"""
    company_name: str
    stock_code: str
    year: int
    report_type: str
    balance_sheet: dict[str, FinancialField]
    income_statement: dict[str, FinancialField]
    cashflow: dict[str, FinancialField]
    validation: ValidationResult
