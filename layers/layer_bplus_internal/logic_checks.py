"""B+层：三步框架逻辑检查（吴林港2022 + 张宁珊2017 + 朱亚萍2015）

Step 1 - 数据层异常扫描: 净现比/收现比、存贷双高、母子资金分离
Step 2 - 经营行为验证:    销售增长 vs 现金流背离、过度投资（国企/民企）
Step 3 - 内控质量评估:    现金流内控定性检查

v3 框架:
- 净现比/收现比: 行业 p75 + 企业规模修正（吴林港2022）
- 存贷双高: 同规模 p75（吴林港2022）
- 母子资金分离: 均值 + 2σ（吴林港2022）
- 新增: 经营现金流/负债比、过度投资检测、内控评分
"""

from typing import Optional
import logging
import yaml
from pathlib import Path

from schemas.financial import FinancialStatement
from schemas.anomaly import LogicAnomaly

logger = logging.getLogger(__name__)

THRESHOLDS_PATH = Path("config/thresholds.yaml")
INDUSTRY_THRESHOLDS_PATH = Path("config/industry_thresholds.yaml")
_INDUSTRY_CACHE: dict | None = None


# ═══════════════════════════════════════════════
# 配置加载
# ═══════════════════════════════════════════════

def _load_thresholds() -> dict:
    if not THRESHOLDS_PATH.exists():
        return {}
    with open(THRESHOLDS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_industry_thresholds(industry: str) -> dict | None:
    global _INDUSTRY_CACHE
    if _INDUSTRY_CACHE is None:
        if not INDUSTRY_THRESHOLDS_PATH.exists():
            _INDUSTRY_CACHE = {}
        else:
            with open(INDUSTRY_THRESHOLDS_PATH, encoding="utf-8") as f:
                _INDUSTRY_CACHE = yaml.safe_load(f).get("industries", {})
    cfg = _INDUSTRY_CACHE.get(industry)
    if cfg and isinstance(cfg, dict) and any(v is not None for v in cfg.values()):
        return cfg
    return _INDUSTRY_CACHE.get("fallback", {}) if isinstance(_INDUSTRY_CACHE, dict) else None


def _get_ind_threshold(
    industry: str | None, key: str, default: float,
    financials: FinancialStatement | None = None,
) -> float:
    """行业 + 规模修正阈值"""
    if not industry:
        return default
    cfg = _load_industry_thresholds(industry)
    if not cfg:
        return default
    val = cfg.get(key)
    if val is None:
        # 自动推算 p75 = p70 × 1.2
        if "p75" in key:
            p70_key = key.replace("p75", "p70")
            p70 = cfg.get(p70_key)
            if p70 is not None:
                return float(p70) * 1.2
        return default
    base = float(val)
    # 规模修正
    size = _detect_size(financials) if financials else "大型"
    is_lower_tail = "p25" in key or "p75" in key  # 现金流类指标：更小=更需放宽
    is_upper_tail = "p75" in key and ("cash_to" in key or "debt_to" in key)
    if size == "小型":
        if is_lower_tail and not is_upper_tail:
            base *= 0.65
        elif is_upper_tail:
            base *= 1.30
    elif size == "中型":
        if is_lower_tail and not is_upper_tail:
            base *= 0.82
        elif is_upper_tail:
            base *= 1.15
    return base


def _detect_size(financials: FinancialStatement | None) -> str:
    if financials is None:
        return "大型"
    assets = _get_value(financials, "Total_Assets", "bs")
    if assets is None:
        return "大型"
    if assets >= 4_000_000_000:
        return "大型"
    elif assets >= 500_000_000:
        return "中型"
    else:
        return "小型"


# ═══════════════════════════════════════════════
# 字段提取
# ═══════════════════════════════════════════════

def _get_value(financials: FinancialStatement, field_name: str, source: str = "any") -> Optional[float]:
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


def _get_ratio(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    return a / b


def _estimate_interest_bearing_debt(financials: FinancialStatement) -> Optional[float]:
    debt_fields = ["Short_Term_Borrowing", "Long_Term_Borrowing",
                    "Bonds_Payable", "Noncurrent_Due_Within_Year"]
    total = 0.0
    found = False
    for f in debt_fields:
        v = _get_value(financials, f, "bs")
        if v is not None:
            total += v; found = True
    return total if found else _get_value(financials, "Total_Liabilities", "bs")


# ═══════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════

def run_all_checks(
    consolidated: FinancialStatement,
    parent: Optional[FinancialStatement] = None,
    industry: Optional[str] = None,
    is_soe: Optional[bool] = None,
) -> list[LogicAnomaly]:
    """三步框架：数据异常 + 经营行为 + 内控评估"""
    thresholds = _load_thresholds()
    ind_cfg = _load_industry_thresholds(industry) if industry else None
    skip = set(ind_cfg.get("skip_checks", []) if ind_cfg else [])
    anomalies: list[LogicAnomaly] = []

    # ── Step 1: 数据层异常 ──
    if "净现比" not in skip:
        a = _check_cash_quality(consolidated, thresholds, industry)
        if a: anomalies.append(a)

    a = _check_dual_high(consolidated, thresholds, industry)
    if a: anomalies.append(a)

    if parent:
        a = _check_fund_separation(consolidated, parent, industry)
        if a: anomalies.append(a)

    # ── Step 2: 经营行为验证 ──
    a = _check_sales_growth_vs_cash_flow(consolidated)
    if a: anomalies.append(a)

    a = _check_overinvestment(consolidated, is_soe)
    if a: anomalies.append(a)

    return anomalies


# ═══════════════════════════════════════════════
# Step 1: 数据层异常 (吴林港2022)
# ═══════════════════════════════════════════════

def _check_cash_quality(
    financials: FinancialStatement, thresholds: dict, industry: str | None,
) -> LogicAnomaly | None:
    """① 净现比/收现比: 两者同时 < 行业 p75 → 利润含金量异常"""
    operating_cf = _get_value(financials, "Operating_Cash_Flow", "cf")
    net_profit = _get_value(financials, "Net_Profit", "pl")
    sales_cash = _get_value(financials, "Operating_Cash_Inflow", "cf")
    revenue = _get_value(financials, "Revenue_Total", "pl")

    net_cash = _get_ratio(operating_cf, net_profit)
    sales_cash_r = _get_ratio(sales_cash, revenue)

    if net_cash is None and sales_cash_r is None:
        return None

    nc_p75 = _get_ind_threshold(industry, "net_profit_cash_ratio_p75", 0.85, financials)
    sc_p75 = _get_ind_threshold(industry, "sales_cash_ratio_p75", 0.95, financials)

    nc_low = net_cash is not None and net_cash < nc_p75
    sc_low = sales_cash_r is not None and sales_cash_r < sc_p75

    # 双条件：两者同时 < p75  OR  两者同时 < 0.6
    nc_below_06 = net_cash is not None and net_cash < 0.6
    sc_below_06 = sales_cash_r is not None and sales_cash_r < 0.6
    both_below_experience = nc_below_06 and sc_below_06

    if not ((nc_low and sc_low) or both_below_experience):
        return None

    nc_p25 = _get_ind_threshold(industry, "net_profit_cash_ratio_p25", 0.25, financials)
    is_severe = (net_cash is not None and net_cash < nc_p25) or (net_cash is not None and net_cash < 0)
    severity = 5.0 if is_severe else 2.5

    return LogicAnomaly(
        check_name="利润含金量异常",
        value=net_cash or sales_cash_r or 0,
        threshold=nc_p75,
        severity=severity,
        summary=f"净现比={net_cash or 'N/A'}（行业p75={nc_p75:.2f}），"
                f"收现比={sales_cash_r or 'N/A'}（行业p75={sc_p75:.2f}），利润含金量异常",
    )


def _check_dual_high(
    financials: FinancialStatement, thresholds: dict, industry: str | None,
) -> LogicAnomaly | None:
    """② 存贷双高: 两者同时 > 同规模 p75 → 资金结构异常"""
    total_assets = _get_value(financials, "Total_Assets", "bs")
    monetary_funds = _get_value(financials, "Monetary_Funds", "bs")
    ib_debt = _estimate_interest_bearing_debt(financials)
    if total_assets is None or monetary_funds is None or ib_debt is None or total_assets == 0:
        return None

    cash_ratio = monetary_funds / total_assets
    debt_ratio = ib_debt / total_assets

    cash_p75 = _get_ind_threshold(industry, "cash_to_assets_p75", 0.30, financials)
    debt_p75 = _get_ind_threshold(industry, "debt_to_assets_p75", 0.42, financials)

    if cash_ratio > cash_p75 and debt_ratio > debt_p75:
        p = cash_ratio * debt_ratio
        return LogicAnomaly(
            check_name="资金结构异常",
            value=p, threshold=cash_p75,
            severity=_calc_dual_high_severity(p),
            summary=f"货币资金/总资产={cash_ratio*100:.1f}%（>同规模p75={cash_p75:.2f}），"
                    f"有息负债/总资产={debt_ratio*100:.1f}%（>同规模p75={debt_p75:.2f}），资金结构异常",
        )
    return None


def _check_fund_separation(
    consolidated: FinancialStatement, parent: FinancialStatement, industry: str | None,
) -> LogicAnomaly | None:
    """③ 母子资金分离度: |差值| > 均值 + 2σ → 资金管控异常"""
    cons_funds = _get_value(consolidated, "Monetary_Funds", "bs")
    parent_funds = _get_value(parent, "Monetary_Funds", "bs")
    if cons_funds is None or parent_funds is None:
        return None

    diff = abs(cons_funds - parent_funds)
    total_assets = _get_value(consolidated, "Total_Assets", "bs") or 1
    ratio = diff / total_assets

    mean_v = _get_ind_threshold(industry, "fund_separ_mean", 0.10, consolidated) if industry else 0.10
    std_v = _get_ind_threshold(industry, "fund_separ_std", 0.06, consolidated) if industry else 0.06
    threshold = mean_v + 2 * std_v

    if ratio < threshold:
        return None

    severity = 5.0 if ratio > threshold * 1.5 else 3.0 if ratio > threshold * 1.25 else 2.0
    return LogicAnomaly(
        check_name="资金管控异常",
        value=ratio, threshold=threshold, severity=severity,
        summary=f"母子资金分离度={ratio*100:.1f}%（>均值+2σ={threshold*100:.1f}%），"
                f"合并报表{cons_funds/1e8:.1f}亿 vs 母公司{parent_funds/1e8:.1f}亿",
    )


# ═══════════════════════════════════════════════
# Step 2: 经营行为验证 (张宁珊2017)
# ═══════════════════════════════════════════════

def _check_sales_growth_vs_cash_flow(
    financials: FinancialStatement,
) -> LogicAnomaly | None:
    """④ 销售增长与现金流背离

    收入增长 > 15% 且 现金流负债比 < 0（或同比下降 > 30%）→ 过度扩张嫌疑
    """
    revenue = _get_value(financials, "Revenue_Total", "pl")
    operating_cf = _get_value(financials, "Operating_Cash_Flow", "cf")
    total_lia = _get_value(financials, "Total_Liabilities", "bs")

    if revenue is None or operating_cf is None or total_lia is None or total_lia == 0:
        return None

    cf_debt_ratio = operating_cf / total_lia
    # 注：同比需去年同期数据，此处用现金流负债比的绝对值判断
    # 简化：现金流负债比 < -0.05 即经营现金流无法覆盖负债

    rev_growth = _estimate_revenue_growth(financials)
    if rev_growth is None:
        return None

    if rev_growth <= 0.15:
        return None  # 增长不显著
    if cf_debt_ratio >= -0.05:
        return None  # 现金流还能支撑

    severity = min(5.0, abs(cf_debt_ratio) * 10 + (rev_growth - 0.15) * 5)
    return LogicAnomaly(
        check_name="过度扩张嫌疑",
        value=cf_debt_ratio, threshold=-0.05, severity=round(severity, 2),
        summary=f"收入增长{rev_growth*100:.1f}%但现金流负债比={cf_debt_ratio:.2f}，"
                f"高增长伴随现金流恶化，存在过度扩张嫌疑",
    )


def _check_overinvestment(
    financials: FinancialStatement, is_soe: bool | None,
) -> LogicAnomaly | None:
    """⑤ 过度投资检测（分企业性质）

    国企: 高销售增长 + 高投资支出 → 过度投资风险
    民企: 高销售增长 + 现金流恶化 → 关注应收恶化/存货积压/利润率下降
    """
    revenue = _get_value(financials, "Revenue_Total", "pl")
    operating_cf = _get_value(financials, "Operating_Cash_Flow", "cf")
    net_profit = _get_value(financials, "Net_Profit", "pl")
    receivables = _get_value(financials, "Accounts_Receivable", "bs")
    inventory = _get_value(financials, "Inventory", "bs")
    total_assets = _get_value(financials, "Total_Assets", "bs")

    if revenue is None or operating_cf is None:
        return None

    rev_growth = _estimate_revenue_growth(financials)
    if rev_growth is None or rev_growth <= 0.10:
        return None

    # 判断投资支出：用 (总资产增长 - 流动资产增长) 近似固定/长期投资增长
    # 简化：直接看经营现金流是否恶化
    cf_decline = operating_cf < 0
    margin = _get_ratio(net_profit, revenue)
    margin_decline = margin is not None and margin < 0.05

    if is_soe is True:
        if rev_growth > 0.10 and cf_decline:
            return LogicAnomaly(
                check_name="过度投资风险", value=rev_growth, threshold=0.10, severity=3.5,
                summary=f"国企收入增长{rev_growth*100:.1f}%但经营现金流恶化，"
                        f"可能存在过度投资风险（张宁珊2017）",
            )
    elif is_soe is False:
        issues = []
        if cf_decline:
            issues.append("现金流恶化")
        if receivables and total_assets and receivables / total_assets > 0.3:
            issues.append("应收账款高企")
        if margin_decline:
            issues.append(f"利润率低({margin:.1%})")
        if issues:
            return LogicAnomaly(
                check_name="民企扩张风险", value=rev_growth, threshold=0.10, severity=2.5,
                summary=f"民企高增长({rev_growth*100:.1f}%)伴随{'、'.join(issues)}，建议关注",
            )
    else:
        # 未分企业性质：通用检测
        if cf_decline and rev_growth > 0.15:
            return LogicAnomaly(
                check_name="销售现金背离", value=rev_growth, threshold=0.15, severity=2.0,
                summary=f"收入增长{rev_growth*100:.1f}%但经营现金流为负，关注增长质量",
            )
    return None


def _estimate_revenue_growth(financials: FinancialStatement) -> float | None:
    """估算收入同比增速（当前期 vs 上期，需 multi-year data）"""
    revenue = _get_value(financials, "Revenue_Total", "pl")
    # 尝试从 balance_sheet 读取上期营收（如有）
    prev = _get_value(financials, "Revenue_Prior_Year", "pl")
    if prev is not None and prev > 0 and revenue is not None:
        return (revenue - prev) / prev
    # 单一期间：返回 None 而非 0，表示无法计算
    return None


# ═══════════════════════════════════════════════
# Step 3: 内控质量评估 (朱亚萍2015) — 独立模块
# ═══════════════════════════════════════════════

def run_internal_control_check(
    has_independent_audit: bool = False,
    has_cash_flow_warning_system: bool = False,
    has_regular_cf_analysis_report: bool = False,
    has_separation_of_duties: bool = False,
) -> tuple[int, str]:
    """⑥ 现金流内控定性检查

    Returns: (缺失项数, 描述)
    """
    checks = {
        "独立内部审计部门": has_independent_audit,
        "现金流预警系统": has_cash_flow_warning_system,
        "定期经营现金流分析报告": has_regular_cf_analysis_report,
        "不相容职务分离": has_separation_of_duties,
    }
    missing = [k for k, v in checks.items() if not v]
    count = len(missing)

    if count >= 3:
        level = "内控薄弱，现金流风险高"
    elif count == 2:
        level = "内控存在明显缺陷"
    elif count == 1:
        level = "内控基本良好"
    else:
        level = "内控体系完善"

    return count, f"缺失{count}/4项: {', '.join(missing)} — {level}"


# ═══════════════════════════════════════════════
# 风险等级汇总
# ═══════════════════════════════════════════════

def calc_risk_level(
    data_anomalies: list[LogicAnomaly],
    behavior_anomalies: list[LogicAnomaly],
    ic_deficiencies: int,
) -> dict:
    """三步框架风险等级判定

    +1: 每个数据层异常（①-③）
    +1: 每个经营行为异常（④-⑤）
    +1: 每项内控缺失（⑥）
    总分 0-6 → 风险等级
    """
    data_score = min(3, len([a for a in data_anomalies if a.check_name in (
        "利润含金量异常", "资金结构异常", "资金管控异常",
    )]))
    behavior_score = min(2, len(behavior_anomalies))
    ic_score = min(2, ic_deficiencies)
    total = data_score + behavior_score + ic_score

    if total >= 5:
        level, label = "高风险", "强烈建议进一步尽调"
    elif total >= 3:
        level, label = "中风险", "建议重点关注"
    elif total >= 1:
        level, label = "低风险", "常规监控"
    else:
        level, label = "正常", "无需特别关注"

    return {
        "total_score": total,
        "data_score": data_score, "behavior_score": behavior_score, "ic_score": ic_score,
        "risk_level": level, "recommendation": label,
    }


# ═══════════════════════════════════════════════
# 严重度
# ═══════════════════════════════════════════════

def _calc_dual_high_severity(product: float) -> float:
    if product > 0.30: return 5.0
    elif product > 0.10: return 2.0 + (product - 0.10) / 0.20 * 3.0
    else: return max(0.5, product / 0.10 * 2.0)


def calc_severity(
    check_name: str, value: float, total_assets: float = 0,
    industry: str | None = None, financials: FinancialStatement | None = None,
) -> float:
    """E1 层调用"""
    if check_name in ("净现比", "利润含金量异常"):
        if value < 0: return 5.0
        nc_p25 = _get_ind_threshold(industry, "net_profit_cash_ratio_p25", 0.25, financials)
        if value >= nc_p25: return 0.0
        return min(5.0, (nc_p25 - value) / max(nc_p25 * 0.3, 0.05) * 3)
    if check_name in ("存贷双高", "资金结构异常"):
        return _calc_dual_high_severity(value)
    if check_name in ("母子资金分离度", "资金管控异常"):
        if value > 0.30: return 5.0
        if value > 0.15: return 3.0
        return max(0.0, value / 0.15 * 1.5)
    if check_name in ("过度扩张嫌疑", "过度投资风险", "民企扩张风险", "销售现金背离"):
        return min(5.0, max(1.5, abs(value) * 8))
    return 0.0
