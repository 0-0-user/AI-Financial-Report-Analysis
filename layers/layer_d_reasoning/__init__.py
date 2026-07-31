"""D层: 原因查找、分配概率、语义匹配"""

from pipeline.step_registry import registry
from pipeline.context import PipelineContext
from pipeline.tracer import tracer
from .d1_dual_path import run_dual_path_analysis
from .d2_probability import run_probability_allocation
from .d3_semantic_matcher import run_semantic_matching


@registry.register("layer_d", requires=["raw_doc", "macro_facts", "tags", "logic_anomalies", "deviations"])
def run(ctx: PipelineContext) -> None:
    """D1 -> D2 -> D3 顺序执行"""
    # D1: 双路径推理 (遍历所有异常)
    reasoning_results = run_dual_path_analysis(ctx)
    tracer.milestone("D1", "双路径推理", "success", f"{len(reasoning_results)} 个异常")

    # D2: D-S 证据理论概率分配
    probability_assignments = run_probability_allocation(
        reasoning_results,
        ctx.macro_facts,
        ctx.tags,
    )
    tracer.milestone("D2", "概率分配", "success", f"{len(probability_assignments)} 个异常")

    # D3: 语义关键词硬性匹配 (为 E1 评分准备 S_base)
    probability_assignments = run_semantic_matching(probability_assignments)
    tracer.milestone("D3", "语义评分", "success", f"{len(probability_assignments)} 个异常")

    ctx.reasoning_results = probability_assignments
