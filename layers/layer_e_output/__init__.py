"""E层: 最终输出打分与报告"""

from pipeline.step_registry import registry
from pipeline.context import PipelineContext
from .e1_scoring import run_scoring
from .e2_report_gen import generate_report


@registry.register("layer_e", requires=["reasoning_results", "deviations", "logic_anomalies", "tags", "financials", "benchmark"])
def run(ctx: PipelineContext) -> None:
    """E1 -> E2 顺序执行"""
    score = run_scoring(ctx)
    ctx.score = score

    report = generate_report(ctx)
    ctx.report = report
