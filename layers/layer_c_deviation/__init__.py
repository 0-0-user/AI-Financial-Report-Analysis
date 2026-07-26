"""C层：算偏差（MAD算法）"""

from pipeline.step_registry import registry
from pipeline.context import PipelineContext
from .mad_calculator import run_deviation_analysis


@registry.register("layer_c")
def run(ctx: PipelineContext) -> None:
    """执行 MAD 偏差计算"""
    if not ctx.benchmark:
        ctx.warnings.append("缺少基准数据，跳过C层")
        return

    deviations = run_deviation_analysis(ctx.financials, ctx.benchmark)
    ctx.deviations = deviations
