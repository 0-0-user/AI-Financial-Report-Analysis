"""B+层：三项逻辑硬伤检查——纯代码，不下定论只出清单

检查项：
1. 净现比/收现比：经营现金流/净利润 和 销售现金/营收，两者都<0.6则可疑
2. 存贷双高：货币资金和有息负债同时占总资产比例过高
3. 母子资金分离度：合并报表与母公司报表的货币资金差额过大

设计约束：
- 纯代码，不调用任何 LLM
- 每一项输出 LogicAnomaly（含 severity 供 E1 层使用）
- 阈值从 config/thresholds.yaml 读取
"""

from typing import Optional
import logging
import yaml
from pathlib import Path

from schemas.financial import FinancialStatement
from schemas.anomaly import LogicAnomaly

logger = logging.getLogger(__name__)

THRESHOLDS_PATH = Path("config/thresholds.yaml")


def _load_thresholds() -> dict:
    """加载阈值配置"""
    if not THRESHOLDS_PATH.exists():
        logger.warning(f"阈值配置文件不存在: {THRESHOLDS_PATH}，使用默认值")
        return {}
    with open(THRESHOLDS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ────────────────────────────────────────────
# 辅助函数：字段提取
# ────────────────────────────────────────────

def _get_value(financials: FinancialStatement, field_name: str, source: str = "any") -> Optional[float]:
    """从 FinancialStatement 中提取指定字段的数值

    Args:
        financials: 财务报表对象
        field_name: 标准字段名（如 Operating_Cash_Flow）
        source: "bs"=资产负债表, "pl"=利润表, "cf"=现金流量表, "any"=自动查找

    Returns:
        字段值（已换算为元），若字段不存在则返回 None
    """
    search_order = {
        "bs": [financials.balance_sheet],
        "pl": [financials.income_statement],
        "cf": [financials.cashflow],
        "any": [financials.balance_sheet, financials.income_statement, financials.cashflow],
    }
    for stmt in search_order.get(source, search_order["any"]):
        field = stmt.get(field_name)
        if field and hasattr(field, "value"):
            return field.value
    return None


def _get_ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    """安全计算比值，分母为 0 或任一为 None 时返回 None"""
    if numerator is None or denominator is None:
        return None
    if denominator == 0:
        return None
    return numerator / denominator


# ────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────

def run_all_checks(
    consolidated: FinancialStatement,
    parent: Optional[FinancialStatement] = None,
) -> list[LogicAnomaly]:
    """执行所有逻辑检查项

    Args:
        consolidated: 合并报表 FinancialStatement
        parent: 母公司报表 FinancialStatement（可选，用于母子资金分离度检查）

    Returns:
        LogicAnomaly 列表（空列表 = 全部通过）
    """
    thresholds = _load_thresholds()
    anomalies: list[LogicAnomaly] = []

    check1 = check_net_profit_cash_ratio(consolidated, thresholds)
    if check1:
        anomalies.append(check1)

    check2 = check_deposit_loan_dual_high(consolidated, thresholds)
    if check2:
        anomalies.append(check2)

    if parent:
        check3 = check_subsidiary_fund_separation(consolidated, parent, thresholds)
        if check3:
            anomalies.append(check3)

    return anomalies


# ────────────────────────────────────────────
# 检查项 1：净现比 / 收现比
# ────────────────────────────────────────────

def check_net_profit_cash_ratio(
    financials: FinancialStatement,
    thresholds: Optional[dict] = None,
) -> Optional[LogicAnomaly]:
    """净现比/收现比联合检查

    净现比 = 经营活动现金流净额 / 净利润
    收现比 = 销售商品收到的现金 / 营业收入

    判定规则：
    - 两者都显著低于 0.6 → 利润含金量低，标记为可疑
    - 净现比 < 0（负现金流）→ 严重
    - 收现比单独偏低 → 回款困难，但不下强结论

    正常情况返回 None
    """
    config = thresholds or _load_thresholds()
    bplus_cfg = config.get("bplus", {})

    # 提取字段
    operating_cf = _get_value(financials, "Operating_Cash_Flow", "cf")
    net_profit = _get_value(financials, "Net_Profit", "pl")
    sales_cash = _get_value(financials, "Operating_Cash_Inflow", "cf")
    revenue = _get_value(financials, "Revenue_Total", "pl")

    net_cash_ratio = _get_ratio(operating_cf, net_profit)
    sales_cash_ratio = _get_ratio(sales_cash, revenue)

    # 提取配置中的阈值
    net_cash_cfg = bplus_cfg.get("净现比", {})
    sales_cash_cfg = bplus_cfg.get("收现比", {})
    net_cash_normal = net_cash_cfg.get("normal_range", [0.6, 2.0])
    net_cash_severe = net_cash_cfg.get("severe_threshold", 0.0)
    sales_cash_normal = sales_cash_cfg.get("normal_range", [0.6, 1.5])

    # 场景：任一比值无法计算
    if net_cash_ratio is None and sales_cash_ratio is None:
        return None

    # 计算异常摘要
    issues: list[str] = []

    # 净现比判断
    if net_cash_ratio is not None and net_cash_ratio < net_cash_normal[0]:
        if net_cash_ratio <= net_cash_severe:
            issues.append(f"净现比严重异常（{net_cash_ratio:.2f}），经营现金流为负或极低")
        else:
            issues.append(f"净现比偏低（{net_cash_ratio:.2f}），低于正常下限{net_cash_normal[0]}")

    # 收现比判断
    if sales_cash_ratio is not None and sales_cash_ratio < sales_cash_normal[0]:
        if sales_cash_ratio < sales_cash_cfg.get("severe_threshold", 0.3):
            issues.append(f"收现比严重偏低（{sales_cash_ratio:.2f}），回款能力堪忧")
        else:
            issues.append(f"收现比偏低（{sales_cash_ratio:.2f}），低于正常下限{sales_cash_normal[0]}")

    if not issues:
        return None

    # 确定严重度
    severity = 0.0
    if net_cash_ratio is not None and net_cash_ratio < 0:
        severity = 5.0  # 经营现金流为负，最严重
    elif net_cash_ratio is not None and net_cash_ratio < net_cash_normal[0]:
        severity = (net_cash_normal[0] - net_cash_ratio) / 0.2 * 2  # 按偏离度计算
        severity = min(5.0, severity)
    elif sales_cash_ratio is not None and sales_cash_ratio < sales_cash_cfg.get("severe_threshold", 0.3):
        severity = 3.0

    return LogicAnomaly(
        check_name="净现比",
        value=net_cash_ratio if net_cash_ratio is not None else sales_cash_ratio or 0,
        threshold=net_cash_normal[0],
        severity=round(severity, 2),
        summary="; ".join(issues),
    )


# ────────────────────────────────────────────
# 检查项 2：存贷双高
# ────────────────────────────────────────────

def check_deposit_loan_dual_high(
    financials: FinancialStatement,
    thresholds: Optional[dict] = None,
) -> Optional[LogicAnomaly]:
    """存贷双高检查

    货币资金/总资产 和 有息负债/总资产 同时处于高位则可疑。

    存贷双高常见于：
    - 集团型公司（资金归集导致合并报表货币资金放大）→ 非造假
    - 房地产公司（行业特性）→ 非造假
    - 财务造假（如康美药业、康得新）→ 造假嫌疑

    判定规则：
    - 两者都 > both_above（默认 0.3）→ 可疑
    - 仅单方面高 → 标记但不强制

    注意：本检查不下定论是造假，最终判断交给 D 层。
    """
    config = thresholds or _load_thresholds()
    dual_high_cfg = config.get("bplus", {}).get("存贷双高", {})
    both_above = dual_high_cfg.get("both_above", 0.3)

    total_assets = _get_value(financials, "Total_Assets", "bs")
    monetary_funds = _get_value(financials, "Monetary_Funds", "bs")

    # 有息负债估算：短期借款 + 长期借款 + 应付债券 + 一年内到期非流动负债
    interest_bearing_debt = _estimate_interest_bearing_debt(financials)

    if total_assets is None or monetary_funds is None or interest_bearing_debt is None:
        return None
    if total_assets == 0:
        return None

    cash_ratio = monetary_funds / total_assets
    debt_ratio = interest_bearing_debt / total_assets

    if cash_ratio < both_above or debt_ratio < both_above:
        return None  # 未同时处于高位

    # 计算严重度
    product = cash_ratio * debt_ratio
    severity = _calc_dual_high_severity(product)
    # 产品值用于 E1 层修正系数
    product_for_severity = product

    return LogicAnomaly(
        check_name="存贷双高",
        value=product_for_severity,
        threshold=both_above,
        severity=severity,
        summary=f"货币资金占总资产{cash_ratio*100:.1f}%，有息负债占总资产{debt_ratio*100:.1f}%，"
                f"两者均超过阈值{both_above*100:.0f}%，存在存贷双高风险",
    )


def _estimate_interest_bearing_debt(financials: FinancialStatement) -> Optional[float]:
    """估算有息负债总额

    使用资产负债表常见字段：
    短期借款 + 长期借款 + 应付债券 + 一年内到期的非流动负债 + 长期应付款（融资租赁部分）

    如果这些字段都不存在，回退到 Total_Liabilities（高估，准确度较低）
    """
    total = 0.0
    found_any = False

    # 常见有息负债字段（按标准名查找）
    debt_fields = [
        "Short_Term_Borrowing",        # 短期借款
        "Long_Term_Borrowing",         # 长期借款
        "Bonds_Payable",               # 应付债券
        "Noncurrent_Due_Within_Year",  # 一年内到期的非流动负债
    ]

    for field in debt_fields:
        val = _get_value(financials, field, "bs")
        if val is not None:
            total += val
            found_any = True

    if found_any:
        return total

    # 降级：用总负债近似（不准确，但比没有好）
    logger.debug("无细分有息负债字段，使用 Total_Liabilities 近似估算")
    return _get_value(financials, "Total_Liabilities", "bs")


def _calc_dual_high_severity(product: float) -> float:
    """存贷双高严重度：基于 cash_ratio * debt_ratio 的乘积"""
    if product > 0.3:
        return 5.0
    elif product > 0.1:
        return 2.0 + (product - 0.1) / 0.2 * 3.0  # 线性插值 2~5
    else:
        return max(0.5, product / 0.1 * 2.0)


# ────────────────────────────────────────────
# 检查项 3：母子资金分离度
# ────────────────────────────────────────────

def check_subsidiary_fund_separation(
    consolidated: FinancialStatement,
    parent: FinancialStatement,
    thresholds: Optional[dict] = None,
) -> Optional[LogicAnomaly]:
    """母子资金分离度检查

    差值 = 合并报表货币资金 - 母公司报表货币资金

    差值极大意味着：钱都在子公司，母公司无法自由支配。
    可能原因：
    - 集团资金归集模式（正常，如大型央企）
    - 上市公司为大股东提供担保/资金占用（造假嫌疑）
    - 子公司为海外主体，受外汇管制（需进一步调查）

    判定规则：
    - 差值/总资产 > 30% → 严重
    - 差值/总资产 > 15% → 可疑
    """
    config = thresholds or _load_thresholds()
    separation_cfg = config.get("bplus", {}).get("母子资金分离度", {})
    severe_ratio = separation_cfg.get("severe_ratio", 0.3)
    warning_ratio = separation_cfg.get("warning_ratio", 0.15)

    consolidated_funds = _get_value(consolidated, "Monetary_Funds", "bs")
    parent_funds = _get_value(parent, "Monetary_Funds", "bs")
    total_assets = _get_value(consolidated, "Total_Assets", "bs")

    if consolidated_funds is None or parent_funds is None:
        return None
    if total_assets is None or total_assets == 0:
        return None

    difference = consolidated_funds - parent_funds
    ratio = difference / total_assets

    if ratio < warning_ratio:
        return None  # 正常范围

    # 确定严重度
    if ratio >= severe_ratio:
        severity = 5.0
        level = "严重"
    else:
        # 线性插值：15% → 2.0, 30% → 5.0
        severity = 2.0 + (ratio - warning_ratio) / (severe_ratio - warning_ratio) * 3.0
        level = "偏高"

    return LogicAnomaly(
        check_name="母子资金分离度",
        value=ratio,
        threshold=warning_ratio,
        severity=round(severity, 2),
        summary=f"合并报表货币资金{consolidated_funds/1e8:.1f}亿，"
                f"母公司货币资金{parent_funds/1e8:.1f}亿，"
                f"差值为{difference/1e8:.1f}亿（占总资产{ratio*100:.1f}%），"
                f"{level}分离",
    )


# ────────────────────────────────────────────
# 严重度计算（供 E1 层调用）
# ────────────────────────────────────────────

def calc_severity(check_name: str, value: float, total_assets: float) -> float:
    """根据具体的逻辑异常类型和数值，计算严重度修正分

    供 E1 层在计算 B+ 扣分时调用。

    Args:
        check_name: 检查项名称（"净现比"/"存贷双高"/"母子资金分离度"）
        value: 异常数值（净现比=实际比值，存贷双高=乘积，母子分离=ratio）
        total_assets: 总资产（部分检查需要，未使用时可传 0）

    Returns:
        严重度分数（0~5）
    """
    if check_name == "净现比":
        # value 为净现比
        if value < 0:
            return 5.0  # 经营现金流为负，直接最高严重度
        return min(5.0, max(0.0, (0.6 - value) / 0.2 * 2))

    if check_name == "收现比":
        # 收现比单独判断
        if value < 0.3:
            return 5.0
        return min(5.0, max(0.0, (0.6 - value) / 0.15 * 3))

    if check_name == "存贷双高":
        # value 为 cash_ratio * debt_ratio 的乘积
        return _calc_dual_high_severity(value)

    if check_name == "母子资金分离度":
        # value 为 (合并货币资金 - 母公司货币资金) / 总资产
        if value > 0.3:
            return 5.0
        if value > 0.15:
            return 3.0
        return max(0.0, value / 0.15 * 1.5)

    return 0.0
