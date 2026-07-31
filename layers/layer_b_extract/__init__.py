"""B层: 提取财务数据"""

from pipeline.step_registry import registry
from pipeline.context import PipelineContext
from pipeline.tracer import tracer


@registry.register("layer_b", requires=["raw_doc"])
def run(ctx: PipelineContext) -> None:
    """B0 -> B1 顺序执行"""
    from .b0_semantic_guide import run_semantic_guide
    from .b1_extractors import run_extraction
    from .b1_validators import run_validation

    guide = run_semantic_guide(ctx.raw_doc)
    n_tables = len(guide.tables)
    n_fields = sum(len(t.field_mappings) for t in guide.tables)
    tracer.milestone("B0", "表头映射", "success", f"识别 {n_tables} 张报表, {n_fields} 个字段映射")

    financials, parent_financials = run_extraction(ctx.raw_doc, guide)
    tracer.milestone(
        "B1", "数值提取", "success",
        f"合并: 资产负债表 {len(financials.balance_sheet)} 字段, 利润表 {len(financials.income_statement)} 字段, 现金流 {len(financials.cashflow)} 字段",
    )

    validation = run_validation(financials)

    if not validation.is_valid:
        tracer.milestone("B1", "勾稽校验", "failed", validation.error_message or "未通过")
        ctx.validation_passed = False
        ctx.errors.append(f"勾稽校验失败: {validation.error_message}")
        return

    tracer.milestone("B1", "勾稽校验", "success", f"通过 ({len(validation.checks)} 项检查)")
    ctx.validation_passed = True
    ctx.financials = financials
    ctx.parent_financials = parent_financials
