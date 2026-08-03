"""A0宏观搜索 (独立步骤) : 在 B 层之后执行，确保有完整的行业标签和财务数据上下文

执行顺序: 
  layer_0 -> layer_a -> layer_b -> layer_amacro -> layer_bplus -> layer_c -> layer_d -> layer_e

前置依赖 (由 Orchestrator 校验) : 
  - raw_doc (Layer 0) 
  - tags (Layer A1) 
  - financials (Layer B) 
"""

from pipeline.step_registry import registry
from pipeline.context import PipelineContext
from layers.layer_a_benchmark.a0_macro_search import run_macro_search


@registry.register("layer_amacro", requires=["raw_doc", "tags", "financials"])
def run(ctx: PipelineContext) -> None:
    """执行宏观搜索（LLM 失败时优雅降级为空）"""
    try:
        ctx.macro_facts = run_macro_search(ctx)
    except Exception as e:
        from pipeline.tracer import tracer
        tracer.milestone("A0", "宏观搜索", "warning", f"LLM不可用, 跳过: {e}")
        ctx.macro_facts = []
