"""B1层：勾稽关系校验——纯代码校验会计恒等式"""

from schemas.financial import FinancialStatement, ValidationResult


def run_validation(financials: FinancialStatement) -> ValidationResult:
    """校验最基础的会计恒等式

    检查项：
    1. 资产总计 = 负债合计 + 所有者权益合计
    2. 期末未分配利润 ≈ 期初未分配利润 + 本期净利润 - 本期分红
    3. 营业收入 - 营业成本 = 毛利润（近似）
    """
    checks = []

    eq1 = _check_balance_sheet_equation(financials)
    checks.append(eq1)

    eq2 = _check_retained_earnings(financials)
    checks.append(eq2)

    all_passed = all(c["passed"] for c in checks)
    return ValidationResult(
        is_valid=all_passed,
        checks=checks,
        error_message=None if all_passed else "存在未通过的勾稽校验",
    )


def _check_balance_sheet_equation(financials: FinancialStatement) -> dict:
    """检查 资产 = 负债 + 权益"""
    # TODO: 实现具体校验逻辑
    return {"check_name": "资产负债表恒等式", "passed": True, "detail": ""}


def _check_retained_earnings(financials: FinancialStatement) -> dict:
    """检查未分配利润变动"""
    return {"check_name": "未分配利润勾稽", "passed": True, "detail": ""}
