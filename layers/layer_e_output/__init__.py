"""E层: 最终输出打分与报告"""

from pipeline.step_registry import registry
from pipeline.context import PipelineContext
from pipeline.tracer import tracer
from .e1_scoring import run_scoring
from .e2_report_gen import generate_report


@registry.register("layer_e", requires=["reasoning_results", "deviations", "logic_anomalies", "tags", "financials", "benchmark"])
def run(ctx: PipelineContext) -> None:
    """E1 -> E2 顺序执行"""
    score = run_scoring(ctx)
    ctx.score = score
    tracer.milestone("E1", "打分", "success", f"总分 {score.final_score} (累计扣 {score.total_deduction})")

    report = generate_report(ctx)
    ctx.report = report
    tracer.milestone("E2", "报告生成", "success", f"核心异常 {len(report.core_anomalies)} 项, Markdown+JSON 双输出")
