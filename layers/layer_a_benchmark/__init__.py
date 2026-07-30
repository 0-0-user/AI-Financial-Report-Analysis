"""A层: 找对比基准 (行业定性) """

from pipeline.step_registry import registry
from pipeline.context import PipelineContext


@registry.register("layer_a", requires=["raw_doc"])
def run(ctx: PipelineContext) -> None:
    """A1 -> A2 顺序执行 (A0 宏观搜索已拆至 layer_amacro，在 B 层后执行) """
    from .a1_tagging import run_tagging
    from .a2_matcher import run_matching

    ctx.tags = run_tagging(ctx.raw_doc)
    benchmark, multi_year, company_names = run_matching(ctx.tags)
    ctx.benchmark = benchmark
    ctx.multi_year_financials = multi_year
    ctx.multi_year_company_names = company_names
