"""B1层: 勾稽关系校验——纯代码校验会计恒等式

核心校验逻辑 (纯数学，不涉及任何语义判断) : 
1. 资产总计 = 负债合计 + 所有者权益合计 (资产负债表恒等式) 
2. 期末未分配利润 ~ 期初未分配利润 + 本期净利润 - 本期分红 (利润勾稽) 

如果校验失败，流水线在 B1 层阻断，直接跳转到 E 层输出系统异常报告。
"""

from schemas.financial import FinancialStatement, ValidationResult, ValidationCheck


# 会计恒等式允许的误差容限 (1%) 
TOLERANCE = 0.01


def run_validation(financials: FinancialStatement) -> ValidationResult:
    """校验最基础的会计恒等式

    检查项: 
    1. 资产总计 = 负债合计 + 所有者权益合计 (误差 < 1%) 
    2. 期末未分配利润 ~ 期初未分配利润 + 本期净利润 - 本期分红

    返回 ValidationResult，含每条检查的详细数值。
    """
    checks = []

    eq1 = _check_balance_sheet_equation(financials)
    checks.append(eq1)

    eq2 = _check_retained_earnings(financials)
    checks.append(eq2)

    all_passed = all(c.passed for c in checks)
    return ValidationResult(
        is_valid=all_passed,
        checks=checks,
        error_message=None if all_passed else "存在未通过的勾稽校验，PDF 数据源可能损坏或造假",
    )


def _check_balance_sheet_equation(financials: FinancialStatement) -> ValidationCheck:
    """检查 资产 = 负债 + 权益

    从 balance_sheet 字典中读取 Total_Assets、Total_Liabilities、Total_Equity。
    如果某字段缺失，标记为不通过。
    """
    bs = financials.balance_sheet

    assets = bs.get("Total_Assets")
    liabilities = bs.get("Total_Liabilities")
    # 兼容两种命名: Equity_Total (标准字段名) 和 Total_Equity (旧名) 
    equity = bs.get("Equity_Total") or bs.get("Total_Equity")

    if not assets or not liabilities or not equity:
        return ValidationCheck(
            check_name="资产负债表恒等式",
            passed=False,
            detail=f"缺少字段: assets={'有' if assets else '无'}, "
                   f"liabilities={'有' if liabilities else '无'}, "
                   f"equity={'有' if equity else '无'}",
        )

    left = assets.value
    right = liabilities.value + equity.value
    diff_ratio = abs(left - right) / max(left, right, 1)

    return ValidationCheck(
        check_name="资产负债表恒等式",
        passed=diff_ratio < TOLERANCE,
        left_value=left,
        right_value=right,
        detail=f"资产={left:.2f}, 负债+权益={right:.2f}, 偏差={diff_ratio*100:.2f}%",
    )


def _check_retained_earnings(financials: FinancialStatement) -> ValidationCheck:
    """检查未分配利润变动: 期末 ~ 期初 + 净利润 - 分红

    如果期初或期末字段缺失，这项检查跳过 (不是所有报表都有这些字段) 。
    """
    bs = financials.balance_sheet
    income = financials.income_statement

    end_retained = bs.get("Retained_Earnings_End")
    begin_retained = bs.get("Retained_Earnings_Begin")
    net_profit = income.get("Net_Profit")
    dividends = cashflow_dividends = bs.get("Dividends_Payable")

    # 如果缺少关键字段，跳过此项检查 (不是强制项) 
    if not end_retained or not begin_retained or not net_profit:
        return ValidationCheck(
            check_name="未分配利润勾稽",
            passed=True,
            detail="跳过: 缺少期初/期末未分配利润或净利润字段",
        )

    expected = begin_retained.value + net_profit.value
    if dividends:
        expected -= dividends.value

    diff_ratio = abs(end_retained.value - expected) / max(abs(expected), 1)

    return ValidationCheck(
        check_name="未分配利润勾稽",
        passed=diff_ratio < TOLERANCE,
        left_value=end_retained.value,
        right_value=expected,
        detail=f"期末={end_retained.value:.2f}, 期望={expected:.2f}, 偏差={diff_ratio*100:.2f}%",
    )
