"""B+层：三项逻辑硬伤检查——纯代码，不下定论只出清单"""

from typing import Optional
from schemas.financial import FinancialStatement
from schemas.anomaly import LogicAnomaly


def run_all_checks(
    consolidated: FinancialStatement,
    parent: Optional[FinancialStatement] = None,
) -> list[LogicAnomaly]:
    """执行所有逻辑检查项"""
    anomalies = []

    check1 = check_net_profit_cash_ratio(consolidated)
    if check1:
        anomalies.append(check1)

    check2 = check_deposit_loan_dual_high(consolidated)
    if check2:
        anomalies.append(check2)

    if parent:
        check3 = check_subsidiary_fund_separation(consolidated, parent)
        if check3:
            anomalies.append(check3)

    return anomalies


def check_net_profit_cash_ratio(financials: FinancialStatement) -> Optional[LogicAnomaly]:
    """净现比/收现比检查

    净现比 = 经营活动现金流净额 / 净利润
    收现比 = 销售商品收到的现金 / 营业收入
    如果两者都 < 0.6，标记为可疑
    """
    # TODO: 提取对应字段并计算
    return None


def check_deposit_loan_dual_high(financials: FinancialStatement) -> Optional[LogicAnomaly]:
    """存贷双高检查

    货币资金/总资产 和 有息负债/总资产 同时处于高位则可疑
    """
    return None


def check_subsidiary_fund_separation(
    consolidated: FinancialStatement,
    parent: FinancialStatement,
) -> Optional[LogicAnomaly]:
    """母子资金分离度检查

    合并报表货币资金 - 母公司报表货币资金，差值极大则可疑
    """
    return None


def calc_severity(check_name: str, value: float, total_assets: float) -> float:
    """计算严重度修正分（供 E1 层调用）"""
    if check_name == "净现比":
        if value < 0:
            return 5.0
        return max(0, (0.6 - value) / 0.2 * 2)
    if check_name == "存贷双高":
        return min(5.0, value / 0.3 * 5)
    if check_name == "母子资金分离度":
        ratio = value / total_assets if total_assets else 0
        if ratio > 0.3:
            return 5.0
        if ratio > 0.15:
            return 3.0
    return 0.0
