"""D1层：双路径推理编排——遍历每个异常同时调用路1和路2"""

from pipeline.context import PipelineContext
from .d1_lookup_notes import lookup_in_annual_report
from .d1_hypothesis import generate_hypotheses


def run_dual_path_analysis(ctx: PipelineContext) -> list[dict]:
    """遍历所有异常（B+ + C），执行双路径推理

    路1（查原文）：从附注和管理层讨论中找客观解释
    路2（推演假设）：基于宏观事实 + 标签 + 偏差发散假设
    """
    results = []

    # 分析 B+ 层异常
    if ctx.logic_anomalies:
        for anomaly in ctx.logic_anomalies:
            lookup = lookup_in_annual_report(ctx.raw_doc, anomaly)
            hypotheses = generate_hypotheses(
                anomaly=anomaly,
                macro_facts=ctx.macro_facts or [],
                tags=ctx.tags,
                deviation=None,
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
                deviation=deviation,
            )
            results.append({
                "source": "C",
                "anomaly": deviation,
                "lookup": lookup,
                "hypotheses": hypotheses,
            })

    return results
