"""B+层：内部逻辑检查（造假初步判断）

包含：
- 基础 3 项（PDF 架构定义）：净现比、存贷双高、母子资金分离度
- 扩展 7 项（代码补充）：应收增速异常、毛利率波动、研发资本化、
  存货堆积、关联交易、非经常性损益、洗大澡嫌疑
"""

from pipeline.step_registry import registry
from pipeline.context import PipelineContext
from .logic_checks import run_all_checks


@registry.register("layer_bplus")
def run(ctx: PipelineContext) -> None:
    """执行 B+ 层逻辑检查（基础 3 项 + 扩展 7 项）"""
    if not ctx.validation_passed:
        ctx.warnings.append("B层校验未通过，跳过B+层检查")
        return

    anomalies = run_all_checks(ctx.financials, ctx.parent_financials)

    # 也运行扩展造假检测
    try:
        from .fraud_patterns import run_extended_checks
        extended = run_extended_checks(ctx.financials)
        anomalies.extend(extended)
    except ImportError:
        pass  # fraud_patterns 可选

    ctx.logic_anomalies = anomalies
