"""B层：提取财务数据"""

from pipeline.step_registry import registry
from pipeline.context import PipelineContext


@registry.register("layer_b")
def run(ctx: PipelineContext) -> None:
    """B0 → B1 顺序执行"""
    from .b0_semantic_guide import run_semantic_guide
    from .b1_extractors import run_extraction
    from .b1_validators import run_validation

    guide = run_semantic_guide(ctx.raw_doc)
    financials = run_extraction(ctx.raw_doc, guide)
    validation = run_validation(financials)

    if not validation.is_valid:
        ctx.validation_passed = False
        ctx.errors.append(f"勾稽校验失败: {validation.error_message}")
        return

    ctx.validation_passed = True
    ctx.financials = financials
