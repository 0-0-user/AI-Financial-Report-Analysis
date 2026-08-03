"""D层: 原因查找、分配概率、语义匹配"""

from pipeline.step_registry import registry
from pipeline.context import PipelineContext
from pipeline.tracer import tracer
from .d1_dual_path import run_dual_path_analysis
from .d2_probability import run_probability_allocation
from .d3_semantic_matcher import run_semantic_matching


@registry.register("layer_d", requires=["raw_doc", "macro_facts", "tags", "logic_anomalies", "deviations"])
def run(ctx: PipelineContext) -> None:
    """D1 -> D2 -> D3 顺序执行（LLM 不可用时优雅降级）"""
    try:
        reasoning_results = run_dual_path_analysis(ctx)
        tracer.milestone("D1", "双路径推理", "success", f"{len(reasoning_results)} 个异常")
    except Exception as e:
        tracer.milestone("D1", "双路径推理", "warning", f"LLM不可用, 降级空推理: {e}")
        reasoning_results = _empty_d1_results(ctx)

    try:
        probability_assignments = run_probability_allocation(reasoning_results, ctx.macro_facts, ctx.tags)
        tracer.milestone("D2", "概率分配", "success", f"{len(probability_assignments)} 个异常")
    except Exception as e:
        tracer.milestone("D2", "概率分配", "warning", f"LLM不可用, 降级默认概率: {e}")
        probability_assignments = _empty_d2_results(reasoning_results)

    try:
        probability_assignments = run_semantic_matching(probability_assignments)
        tracer.milestone("D3", "语义评分", "success", f"{len(probability_assignments)} 个异常")
    except Exception as e:
        tracer.milestone("D3", "语义评分", "warning", f"LLM不可用, 跳过: {e}")

    ctx.reasoning_results = probability_assignments


def _empty_d1_results(ctx: PipelineContext) -> list[dict]:
    """LLM 不可用时返回空推理结果"""
    results = []
    from schemas.reasoning import Explanation, Hypothesis
    for a in (ctx.logic_anomalies or []):
        results.append({"source": "B+", "anomaly": a, "lookup": [], "hypotheses": []})
    for d in (ctx.deviations or []):
        results.append({"source": "C", "anomaly": d, "lookup": [], "hypotheses": []})
    return results


def _empty_d2_results(reasoning_results: list[dict]) -> list:
    """LLM 不可用时返回默认概率"""
    from schemas.reasoning import ProbabilityAssignment
    assignments = []
    for r in reasoning_results:
        a = r["anomaly"]
        ind = getattr(a, "indicator", getattr(a, "check_name", "unknown"))
        assignments.append(ProbabilityAssignment(
            anomaly_indicator=ind, anomaly_source=r["source"],
            lookups=r.get("lookup", []), hypotheses=r.get("hypotheses", []),
            probabilities={"信息不足": 1.0},
        ))
    return assignments
