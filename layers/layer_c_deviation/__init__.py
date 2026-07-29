"""C层：算偏差（MAD算法）"""

from pipeline.step_registry import registry
from pipeline.context import PipelineContext
from .mad_calculator import run_deviation_analysis


@registry.register("layer_c", requires=["financials", "benchmark"])
def run(ctx: PipelineContext) -> None:
    """执行 MAD 偏差计算"""
    deviations = run_deviation_analysis(ctx.financials, ctx.benchmark)
    ctx.deviations = deviations
