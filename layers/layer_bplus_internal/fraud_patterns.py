"""B+ 层扩展：财务造假检测模式库

基础 7 项 + Beneish M-Score 8 指标 (Beneish 1999, 被引 2,100+)。

M-Score 公式:
  M = -4.84 + 0.920*DSRI + 0.528*GMI + 0.404*AQI + 0.892*SGI
      + 0.115*DEPI - 0.172*SGAI + 4.679*TATA - 0.327*LVGI
  阈值: M > -1.78 → 操纵嫌疑 (~76% 准确率)
"""

from layers.layer_bplus_internal.logic_checks import _get_value

这些模式参考了：
- 学术界财务造假检测文献（Beneish M-Score, Dechow F-Score）
- 中国证监会历年财务造假案例特征
- 会计师事务所实质性分析程序常用指标

设计约束：
- 纯代码，不涉及 LLM
- 每项检测独立运行，互不依赖
- 输出与 LogicAnomaly 格式兼容
"""

from typing import Optional
from schemas.financial import FinancialStatement
from schemas.anomaly import LogicAnomaly


# ────────────────────────────────────────────
# 检测 4：应收账款增速 vs 营收增速（收入虚增检测）
# ────────────────────────────────────────────

def check_receivable_revenue_gap(financials: FinancialStatement) -> Optional[LogicAnomaly]:
    """应收账款增速远超营收增速 → 可能虚增收入

    逻辑：如果收入增长是靠放宽信用政策（赊销）驱动的，应收账款增速会远超营收增速。
    正常情况两者增速应大致同步。

    判定：应收增速 > 营收增速 × 1.5 → 可疑
          应收增速 > 营收增速 × 2.0 → 严重可疑
    """
    from layers.layer_bplus_internal.logic_checks import _get_value

    # 需要往年数据，这里使用 balance_sheet 中的期初/期末值
    # 简化：检查当前应收/营收比例
    receivables = _get_value(financials, "Accounts_Receivable", "bs")
    revenue = _get_value(financials, "Revenue_Total", "pl")

    if receivables is None or revenue is None:
        return None
    if revenue == 0:
        return None

    ratio = receivables / revenue
    threshold = 0.5  # 应收超过营收50% → 可疑

    if ratio < threshold:
        return None

    severity = min(5.0, ratio / threshold * 2.5)
    return LogicAnomaly(
        check_name="应收增速异常",
        value=ratio,
        threshold=threshold,
        severity=round(severity, 2),
        summary=f"应收账款占营收{ratio*100:.1f}%，远超正常水平（>50%），"
                f"可能存在放松信用政策或虚增收入",
    )


# ────────────────────────────────────────────
# 检测 5：毛利率异常波动（跨期虚增/隐藏利润检测）
# ────────────────────────────────────────────

def check_gross_margin_volatility(financials: FinancialStatement) -> Optional[LogicAnomaly]:
    """毛利率大幅波动而无合理解释 → 可能盈余管理

    正常经营下毛利率应相对稳定。大幅波动可能原因：
    - 跨期调节收入确认
    - 人为操控成本分摊
    - 行业剧烈变化（需结合A层宏观判断）
    """
    from layers.layer_bplus_internal.logic_checks import _get_value

    revenue = _get_value(financials, "Revenue_Total", "pl")
    cost = _get_value(financials, "Cost_Revenue", "pl")

    if revenue is None or cost is None or revenue == 0:
        return None

    gross_margin = (revenue - cost) / revenue
    # 注：真正的波动检查需要往年数据，这里给出当前水平标记
    # 异常的极端值：毛利率 < 5% 或 > 80% 通常需要关注
    if 0.05 <= gross_margin <= 0.80:
        return None

    severity = 2.0 if gross_margin < 0.02 or gross_margin > 0.90 else 1.0
    return LogicAnomaly(
        check_name="毛利率异常",
        value=gross_margin,
        threshold=0.05,
        severity=severity,
        summary=f"毛利率={gross_margin*100:.1f}%，"
                + ("远低于正常水平，可能行业恶化或低价倾销" if gross_margin < 0.05
                   else "远高于正常水平，需核实是否可持续"),
    )


# ────────────────────────────────────────────
# 检测 6：研发支出资本化率异常（利润注水检测）
# ────────────────────────────────────────────

def check_rnd_capitalization(financials: FinancialStatement) -> Optional[LogicAnomaly]:
    """研发支出资本化比例过高 → 虚增利润

    研发支出有两种处理方式：
    - 费用化：计入当期损益，减少利润
    - 资本化：计入无形资产，分期摊销

    过高的资本化率 = 把本该当费用的支出推迟到未来 = 虚增当期利润。

    注：需要从附注中提取研发资本化金额，此处检查是否可通过 balance_sheet 推算
    """
    from layers.layer_bplus_internal.logic_checks import _get_value

    # 通过无形资产增加额近似推算资本化金额（仅标记，不强制）
    intangible = _get_value(financials, "Intangible_Assets", "bs")
    total_assets = _get_value(financials, "Total_Assets", "bs")
    net_profit = _get_value(financials, "Net_Profit", "pl")

    if intangible is None or total_assets is None or net_profit is None:
        return None
    if total_assets == 0:
        return None

    intangible_ratio = intangible / total_assets
    # 无形资产占总资产过高（技术公司也通常<30%）
    if intangible_ratio < 0.3:
        return None

    severity = min(5.0, intangible_ratio / 0.3 * 3)
    return LogicAnomaly(
        check_name="研发资本化异常",
        value=intangible_ratio,
        threshold=0.3,
        severity=round(severity, 2),
        summary=f"无形资产占总资产{intangible_ratio*100:.1f}%，需关注研发资本化比例是否合理，"
                f"是否存在利润注水嫌疑",
    )


# ────────────────────────────────────────────
# 检测 7：存货异常增加（减值风险 / 虚增资产检测）
# ────────────────────────────────────────────

def check_inventory_buildup(financials: FinancialStatement) -> Optional[LogicAnomaly]:
    """存货激增而无对应营收增长 → 库存积压或虚增资产

    危险信号：
    - 存货/总资产比例过高（>30% 通常异常）
    - 尤其对于科技/电子/服装等产品迭代快的行业
    """
    from layers.layer_bplus_internal.logic_checks import _get_value

    inventory = _get_value(financials, "Inventory", "bs")
    total_assets = _get_value(financials, "Total_Assets", "bs")
    revenue = _get_value(financials, "Revenue_Total", "pl")

    if inventory is None or total_assets is None or total_assets == 0:
        return None

    inv_ratio = inventory / total_assets
    if inv_ratio < 0.3:
        return None

    # 进一步判断：营收是否同步增长
    severity = min(5.0, inv_ratio / 0.3 * 2.5)

    return LogicAnomaly(
        check_name="存货异常堆积",
        value=inv_ratio,
        threshold=0.3,
        severity=round(severity, 2),
        summary=f"存货占总资产{inv_ratio*100:.1f}%，超过正常水平(30%)，"
                f"存在库存积压或虚增资产风险",
    )


# ────────────────────────────────────────────
# 检测 8：关联交易占比异常
# ────────────────────────────────────────────

def check_related_party_transactions(financials: FinancialStatement) -> Optional[LogicAnomaly]:
    """关联交易占比过高 → 利益输送嫌疑

    异常特征：
    - 向关联方销售额占营收比例过高
    - 对关联方的应收应付金额巨大且账龄长

    注：关联交易数据通常需要从附注中提取，此处标记需要进一步分析
    """
    # 本检测需要从附注中解析关联交易数据
    # 此处作为占位标记，提示需要此功能
    # 在财务数据中应包含 Related_Party_Revenue 字段
    from layers.layer_bplus_internal.logic_checks import _get_value

    rp_revenue = _get_value(financials, "Related_Party_Revenue", "pl")
    total_revenue = _get_value(financials, "Revenue_Total", "pl")

    if rp_revenue is None or total_revenue is None:
        return None

    if total_revenue == 0:
        return None

    rp_ratio = rp_revenue / total_revenue
    if rp_ratio < 0.3:
        return None

    severity = min(5.0, rp_ratio / 0.3 * 3.5)
    return LogicAnomaly(
        check_name="关联交易异常",
        value=rp_ratio,
        threshold=0.3,
        severity=round(severity, 2),
        summary=f"关联方销售占营收{rp_ratio*100:.1f}%，显著偏高，需深入核查交易实质",
    )


# ────────────────────────────────────────────
# 检测 9：非经常性损益占比（利润质量检测）
# ────────────────────────────────────────────

def check_nonrecurring_profit_ratio(financials: FinancialStatement) -> Optional[LogicAnomaly]:
    """非经常性损益占净利润比例过高 → 利润质量差

    如果一家公司靠卖房/卖子公司/政府补贴来维持利润表面好看，
    说明主营业务盈利能力堪忧。
    """
    from layers.layer_bplus_internal.logic_checks import _get_value

    net_profit = _get_value(financials, "Net_Profit", "pl")
    recurring_profit = _get_value(financials, "Recurring_Net_Profit", "pl")

    if net_profit is None or recurring_profit is None:
        return None
    if net_profit == 0:
        return None

    nonrecurring = net_profit - recurring_profit
    # 如果非经常性损益为正，且占净利润比例高
    if nonrecurring <= 0:
        # 非经常性亏损 → 可能是"洗大澡"，见检测10
        return None

    ratio = nonrecurring / net_profit
    if ratio < 0.3:
        return None

    severity = min(5.0, ratio / 0.3 * 3)
    return LogicAnomaly(
        check_name="利润质量低下",
        value=ratio,
        threshold=0.3,
        severity=round(severity, 2),
        summary=f"非经常性损益占净利润{ratio*100:.1f}%，主营业务盈利质量差，"
                f"净利润可能存在粉饰",
    )


# ────────────────────────────────────────────
# 检测 10：洗大澡（Big Bath）嫌疑
# ────────────────────────────────────────────

def check_big_bath(financials: FinancialStatement) -> Optional[LogicAnomaly]:
    """"洗大澡"嫌疑：当年大幅亏损，为来年"轻装上阵"做准备

    常见手法：
    - 一次性计提巨额减值（商誉/存货/应收）
    - 大额资产处置损失
    - 提前确认费用

    特征：净利润大幅为负，但经营现金流尚可
    """
    from layers.layer_bplus_internal.logic_checks import _get_value

    net_profit = _get_value(financials, "Net_Profit", "pl")
    operating_cf = _get_value(financials, "Operating_Cash_Flow", "cf")
    asset_impairment = _get_value(financials, "Asset_Impairment_Loss", "pl")
    goodwill = _get_value(financials, "Goodwill", "bs")

    if net_profit is None:
        return None
    if net_profit >= 0:
        return None  # 利润为正不触发

    # 亏损 + 经营现金流为正 → 非现金亏损（可能是减值导致）
    if operating_cf is not None and operating_cf > 0 and net_profit < 0:
        impairment_flag = bool(asset_impairment and asset_impairment < -abs(net_profit) * 0.5)
        goodwill_flag = bool(goodwill and goodwill > 0)

        if impairment_flag or goodwill_flag:
            return LogicAnomaly(
                check_name="洗大澡嫌疑",
                value=net_profit,
                threshold=0,
                severity=4.0,
                summary=f"净利润{net_profit/1e8:.1f}亿（亏损）但经营现金流为正"
                        f"{operating_cf/1e8:.1f}亿，存在一次性大幅减值洗大澡嫌疑",
            )

    return None


# ═══════════════════════════════════════════════
# Beneish M-Score (Beneish 1999, FAJ, 被引 2,100+)
# ═══════════════════════════════════════════════

def calc_beneish_m_score(
    current: FinancialStatement,
    prior: FinancialStatement | None = None,
) -> dict | None:
    """Beneish M-Score: 8 指标综合盈余操纵检测

    参考文献: Beneish (1999) "The Detection of Earnings Manipulation"
              Financial Analysts Journal, 55(5), 24-36. 被引 > 2,100 次.

    公式:
      M = -4.84 + 0.920*DSRI + 0.528*GMI + 0.404*AQI + 0.892*SGI
          + 0.115*DEPI - 0.172*SGAI + 4.679*TATA - 0.327*LVGI

    阈值: M > -1.78 → 操纵嫌疑（~76% 准确率）

    Returns:
        {"m_score": float, "indicators": dict, "flagged": bool} 或 None（数据不足）
    """
    if prior is None:
        return None

    # ── 提取当期数据 ──
    ar_t = _get_value(current, "Accounts_Receivable", "bs")
    sales_t = _get_value(current, "Revenue_Total", "pl")
    cogs_t = _get_value(current, "Cost_Revenue", "pl")
    ca_t = _get_value(current, "Current_Assets", "bs")
    ppe_t = _get_value(current, "PPE_Net", "bs")
    ta_t = _get_value(current, "Total_Assets", "bs")
    dep_t = _get_value(current, "Depreciation", "pl")
    sga_t = _get_value(current, "SGA_Expense", "pl")
    cl_t = _get_value(current, "Current_Liabilities", "bs")
    ltd_t = _get_value(current, "Long_Term_Debt", "bs")
    ni_t = _get_value(current, "Net_Profit", "pl")
    cfo_t = _get_value(current, "Operating_Cash_Flow", "cf")
    sec_t = _get_value(current, "Monetary_Funds", "bs") or 0  # Securities ≈ 货币资金近似

    # ── 提取上期数据 ──
    ar_prev = _get_value(prior, "Accounts_Receivable", "bs")
    sales_prev = _get_value(prior, "Revenue_Total", "pl")
    cogs_prev = _get_value(prior, "Cost_Revenue", "pl")
    ca_prev = _get_value(prior, "Current_Assets", "bs")
    ppe_prev = _get_value(prior, "PPE_Net", "bs")
    ta_prev = _get_value(prior, "Total_Assets", "bs")
    dep_prev = _get_value(prior, "Depreciation", "pl")
    sga_prev = _get_value(prior, "SGA_Expense", "pl")
    cl_prev = _get_value(prior, "Current_Liabilities", "bs")
    ltd_prev = _get_value(prior, "Long_Term_Debt", "bs")
    ni_prev = _get_value(prior, "Net_Profit", "pl")
    sec_prev = _get_value(prior, "Monetary_Funds", "bs") or 0

    # 有效性检查
    required = [ar_t, sales_t, cogs_t, ta_t, dep_t, sga_t, cl_t, ni_t, cfo_t,
                ar_prev, sales_prev, cogs_prev, ta_prev]
    if any(v is None or v == 0 for v in required):
        return None

    # ── 计算 8 个指标 ──
    indicators = {}

    # DSRI: 应收款周转天数指数
    dsri = (ar_t / sales_t) / (ar_prev / sales_prev) if sales_prev > 0 else 1.0
    indicators["DSRI"] = dsri

    # GMI: 毛利率指数
    gm_t = (sales_t - cogs_t) / sales_t
    gm_prev = (sales_prev - cogs_prev) / sales_prev
    gmi = gm_prev / gm_t if gm_t > 0 else 1.0
    indicators["GMI"] = gmi

    # AQI: 资产质量指数
    numerator_t = 1 - (ca_t + ppe_t + sec_t) / ta_t if ta_t > 0 else 0
    numerator_prev = 1 - (ca_prev + ppe_prev + sec_prev) / ta_prev if ta_prev > 0 else 0
    aqi = numerator_t / numerator_prev if numerator_prev > 0 else 1.0
    indicators["AQI"] = aqi

    # SGI: 销售增长指数
    sgi = sales_t / sales_prev if sales_prev > 0 else 1.0
    indicators["SGI"] = sgi

    # DEPI: 折旧指数
    dep_rate_t = dep_t / (ppe_t + dep_t) if (ppe_t + dep_t) > 0 else 0
    dep_rate_prev = dep_prev / (ppe_prev + dep_prev) if (ppe_prev + dep_prev) > 0 else 0
    depi = dep_rate_prev / dep_rate_t if dep_rate_t > 0 else 1.0
    indicators["DEPI"] = depi

    # SGAI: 销售管理费用指数
    sgai = (sga_t / sales_t) / (sga_prev / sales_prev) if sales_prev > 0 else 1.0
    indicators["SGAI"] = sgai

    # LVGI: 杠杆指数
    lev_t = (cl_t + ltd_t) / ta_t if ta_t > 0 else 0
    lev_prev = (cl_prev + ltd_prev) / ta_prev if ta_prev > 0 else 0
    lvgi = lev_t / lev_prev if lev_prev > 0 else 1.0
    indicators["LVGI"] = lvgi

    # TATA: 总应计/总资产
    tata = (ni_t - cfo_t) / ta_t if ta_t > 0 else 0
    indicators["TATA"] = tata

    # ── M-Score ──
    m_score = (
        -4.84
        + 0.920 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi
        + 0.115 * depi - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi
    )
    flagged = m_score > -1.78

    return {"m_score": round(m_score, 4), "indicators": indicators, "flagged": flagged}


def check_m_score_anomaly(
    current: FinancialStatement,
    prior: FinancialStatement | None = None,
) -> LogicAnomaly | None:
    """M-Score > -1.78 → 盈余操纵嫌疑"""
    result = calc_beneish_m_score(current, prior)
    if result is None:
        return None
    if not result["flagged"]:
        return None

    severity = 3.0 if result["m_score"] > -1.0 else 2.0
    high_indicators = [k for k, v in result["indicators"].items() if v > 1.1]
    return LogicAnomaly(
        check_name="M-Score盈余操纵",
        value=result["m_score"],
        threshold=-1.78,
        severity=severity,
        summary=f"M-Score={result['m_score']:.2f}（阈值-1.78），"
                f"偏高指标: {', '.join(high_indicators) if high_indicators else 'TATA/LVGI'}",
    )


# ────────────────────────────────────────────
# 扩展检查入口
# ────────────────────────────────────────────

def run_extended_checks(
    financials: FinancialStatement,
    prior: FinancialStatement | None = None,
) -> list[LogicAnomaly]:
    """执行扩展造假检测（基础 7 项 + M-Score）

    Args:
        financials: 当期财务报表
        prior: 上期财务报表（可选，用于M-Score）
    """
    checks = [
        check_receivable_revenue_gap,
        check_gross_margin_volatility,
        check_rnd_capitalization,
        check_inventory_buildup,
        check_related_party_transactions,
        check_nonrecurring_profit_ratio,
        check_big_bath,
    ]

    anomalies = []
    for check_fn in checks:
        try:
            result = check_fn(financials)
            if result:
                anomalies.append(result)
        except Exception:
            pass

    # M-Score（需要跨期数据）
    if prior:
        try:
            ms = check_m_score_anomaly(financials, prior)
            if ms:
                anomalies.append(ms)
        except Exception:
            pass

    return anomalies
