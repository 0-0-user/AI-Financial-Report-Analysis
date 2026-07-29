"""D1层：双路径推理编排——遍历每个异常同时调用路1和路2

两路隔离边界：
  路1（原文搬运）: raw_doc + 异常清单，不调自身常识，不做推测
  路2（外部推演）: 异常清单 + 行业标签 + A0宏观事实 + 公司基本情况 + LLM常识
  两路知识来源完全正交，互不越界。
"""

from pipeline.context import PipelineContext
from .d1_lookup_notes import lookup_in_annual_report
from .d1_hypothesis import generate_hypotheses


def run_dual_path_analysis(ctx: PipelineContext) -> list[dict]:
    """遍历所有异常（B+ + C），执行双路径推理

    路1（原文搬运）：lookup_in_annual_report
      - 可使用知识：ctx.raw_doc + 异常清单
      - 不可使用：ctx.macro_facts, ctx.tags（任何 A 层前置数据）
      - LLM 角色：纯搬运——从原文中找客观解释，不做推测

    路2（外部推演）：generate_hypotheses
      - 可使用知识：异常清单 + ctx.tags.hard_tags + ctx.macro_facts + 公司基本情况
      - 不可使用：ctx.raw_doc、tags.financial_profile
      - LLM 角色：发散式——基于前置知识和自身常识推演假设

    隔离原则：两路的知识来源完全正交，互不越界。
    """
    # 从 raw_doc 提取公司基本情况（仅路2使用）
    business_desc = ""
    if ctx.raw_doc and hasattr(ctx.raw_doc, "company_overview"):
        business_desc = getattr(ctx.raw_doc.company_overview, "business_description", "")

    results = []

    # 分析 B+ 层异常
    if ctx.logic_anomalies:
        for anomaly in ctx.logic_anomalies:
            lookup = lookup_in_annual_report(ctx.raw_doc, anomaly)
            hypotheses = generate_hypotheses(
                anomaly=anomaly,
                macro_facts=ctx.macro_facts or [],
                tags=ctx.tags,
                business_desc=business_desc,
            )
            results.append({
                "source": "B+",
                "anomaly": anomaly,
                "lookup": lookup,
                "hypotheses": hypotheses,
            })

    # 分析 C 层异常
    if ctx.deviations:
        for deviation in ctx.deviations:
            lookup = lookup_in_annual_report(ctx.raw_doc, deviation)
            hypotheses = generate_hypotheses(
                anomaly=deviation,
                macro_facts=ctx.macro_facts or [],
                tags=ctx.tags,
                business_desc=business_desc,
            )
            results.append({
                "source": "C",
                "anomaly": deviation,
                "lookup": lookup,
                "hypotheses": hypotheses,
            })

    return results
