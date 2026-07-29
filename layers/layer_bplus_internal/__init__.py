"""B+层：三步框架逻辑检查

Step 1: 数据层异常扫描 (吴林港2022)
  - 净现比/收现比 → 利润含金量异常
  - 存贷双高 → 资金结构异常
  - 母子资金分离 → 资金管控异常

Step 2: 经营行为验证 (张宁珊2017)
  - 销售增长 vs 现金流背离 → 过度扩张嫌疑
  - 过度投资检测（国企/民企分判）

Step 3: 内控质量评估 (朱亚萍2015)
  - 4 项定性检查 → 内控薄弱度

最终: 6 分制风险等级
"""

from pipeline.step_registry import registry
from pipeline.context import PipelineContext
from .logic_checks import run_all_checks, run_internal_control_check, calc_risk_level


@registry.register("layer_bplus")
def run(ctx: PipelineContext) -> None:
    if not ctx.validation_passed:
        ctx.warnings.append("B层校验未通过，跳过B+层检查")
        return

    # 行业
    industry = None
    if ctx.tags and ctx.tags.hard_tags:
        for ht in ctx.tags.hard_tags:
            if ht.system == "同花顺三级行业" and ht.value:
                industry = ht.value; break

    # 国企/民企判断（从公司名称或标签推断）
    is_soe = _detect_soe(ctx)

    # Step 1+2: 数据异常 + 经营行为
    all_anomalies = run_all_checks(
        ctx.financials, ctx.parent_financials,
        industry=industry, is_soe=is_soe,
    )

    # 扩展检测
    try:
        from .fraud_patterns import run_extended_checks
        all_anomalies.extend(run_extended_checks(ctx.financials))
    except ImportError:
        pass

    # Step 3: 内控检查（从年报元数据推断）
    # 默认值：大型企业通常有独立审计和职责分离，小型较难保证
    has_audit = _guess_audit(ctx)
    has_warning = _guess_warning_system(ctx)
    has_report = _guess_cf_report(ctx)
    has_sep = _guess_separation(ctx)
    ic_missing, ic_desc = run_internal_control_check(
        has_audit, has_warning, has_report, has_sep,
    )

    # 分类异常
    data_anomalies = [a for a in all_anomalies if a.check_name in (
        "利润含金量异常", "资金结构异常", "资金管控异常",
        "净现比", "收现比", "存贷双高", "母子资金分离度",
    )]
    behavior_anomalies = [a for a in all_anomalies if a.check_name in (
        "过度扩张嫌疑", "过度投资风险", "民企扩张风险", "销售现金背离",
    )]

    # 风险汇总
    risk = calc_risk_level(data_anomalies, behavior_anomalies, ic_missing)

    ctx.logic_anomalies = all_anomalies
    ctx.warnings.append(
        f"B+风险等级: {risk['risk_level']} (评分{risk['total_score']}/6) — {risk['recommendation']}"
    )
    if ic_missing > 0:
        ctx.warnings.append(f"内控: {ic_desc}")


def _detect_soe(ctx: PipelineContext) -> bool | None:
    """推断企业性质：国企/民企"""
    if ctx.tags and ctx.tags.financial_profile:
        for ht in ctx.tags.hard_tags:
            if ht.value in ("军工电子", "航空装备"):
                return True
    name = (ctx.raw_doc.company_overview.company_name or "") if ctx.raw_doc else ""
    soe_kw = ["中国", "国家", "中央", "集团", "控股集团"]
    if any(kw in name for kw in soe_kw):
        return True
    return None


def _guess_audit(ctx: PipelineContext) -> bool:
    """年报中是否提及独立审计/内审部门"""
    text = ""
    if ctx.raw_doc:
        text = ctx.raw_doc.management_discussion.sections[0].content if ctx.raw_doc.management_discussion.sections else ""
        text += ctx.raw_doc.company_overview.business_description or ""
    audit_kw = ["审计", "内审", "内部控制", "事务所", "会计师事务所"]
    return any(kw in text for kw in audit_kw)


def _guess_warning_system(ctx: PipelineContext) -> bool:
    text = ""
    if ctx.raw_doc:
        text = ctx.raw_doc.management_discussion.sections[0].content if ctx.raw_doc.management_discussion.sections else ""
    return any(kw in text for kw in ["预警", "风险预警", "现金流预警", "监控系统"])


def _guess_cf_report(ctx: PipelineContext) -> bool:
    text = ""
    if ctx.raw_doc:
        for s in (ctx.raw_doc.management_discussion.sections or [])[:3]:
            text += s.content or ""
    return any(kw in text for kw in ["现金流分析", "现金流量分析", "资金分析"])


def _guess_separation(ctx: PipelineContext) -> bool:
    text = ""
    if ctx.raw_doc:
        text = ctx.raw_doc.company_overview.business_description or ""
    return any(kw in text for kw in ["不相容", "职责分离", "内控", "审批流程"])
