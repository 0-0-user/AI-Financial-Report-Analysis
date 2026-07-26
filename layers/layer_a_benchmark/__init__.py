"""A层：找对比基准（行业定性）"""

from pipeline.step_registry import registry
from pipeline.context import PipelineContext


@registry.register("layer_a")
def run(ctx: PipelineContext) -> None:
    """A0 → A1 → A2 顺序执行"""
    from .a0_macro_search import run_macro_search
    from .a1_tagging import run_tagging
    from .a2_matcher import run_matching

    ctx.macro_facts = run_macro_search(ctx.raw_doc)
    ctx.tags = run_tagging(ctx.raw_doc)
    ctx.benchmark = run_matching(ctx.tags)
