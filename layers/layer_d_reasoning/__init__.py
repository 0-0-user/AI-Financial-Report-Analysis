"""D层：原因查找与分配概率"""

from pipeline.step_registry import registry
from pipeline.context import PipelineContext
from .d1_dual_path import run_dual_path_analysis
from .d2_probability import run_probability_allocation


@registry.register("layer_d")
def run(ctx: PipelineContext) -> None:
    """D1 → D2 顺序执行"""
    # D1: 双路径推理（遍历所有异常）
    reasoning_results = run_dual_path_analysis(ctx)

    # D2: 概率分配
    probability_assignments = run_probability_allocation(
        reasoning_results,
        ctx.macro_facts,
        ctx.tags,
    )

    ctx.reasoning_results = probability_assignments
