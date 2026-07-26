"""B+层：内部逻辑检查（造假初步判断）"""

from pipeline.step_registry import registry
from pipeline.context import PipelineContext
from .logic_checks import run_all_checks


@registry.register("layer_bplus")
def run(ctx: PipelineContext) -> None:
    """执行 B+ 层三项逻辑检查"""
    if not ctx.validation_passed:
        ctx.warnings.append("B层校验未通过，跳过B+层检查")
        return

    anomalies = run_all_checks(ctx.financials, ctx.parent_financials)
    ctx.logic_anomalies = anomalies
