"""C层: 算偏差 (MAD算法) """

from pipeline.step_registry import registry
from pipeline.context import PipelineContext
from pipeline.tracer import tracer
from .mad_calculator import run_deviation_analysis


def _company_history(ctx: PipelineContext) -> dict[str, list[float]]:
    """该公司自己的多年指标序列 {东财指标代码: [旧 -> 新]}

    A2 切片出来的 multi_year_financials 是**年份降序**的原始行，而 Pettitt
    是在时间顺序上找变点 —— 顺序反了，"突变在哪一点"整个倒过来。这里按
    year 升序重排，不把顺序当上游的隐含约定。
    """
    if not ctx.multi_year_financials or not ctx.tags:
        return {}
    rows = ctx.multi_year_financials.get(ctx.tags.stock_code)
    if not rows:
        return {}

    series: dict[str, list[float]] = {}
    for row in sorted(rows, key=lambda r: str(r.get("year") or "")):
        for key, val in row.items():
            if key == "year" or val is None:
                continue
            try:
                series.setdefault(key, []).append(float(val))
            except (TypeError, ValueError):
                continue
    # 少于 4 个点的序列 Pettitt 判不了 (detect_temporal_anomaly 自己也会拒),
    # 在这里先清掉, 免得下游拿一段"看着有、其实判不了"的序列反复空转。
    return {k: v for k, v in series.items() if len(v) >= 4}


@registry.register("layer_c", requires=["financials", "benchmark"])
def run(ctx: PipelineContext) -> None:
    """执行 MAD 偏差计算"""
    deviations = run_deviation_analysis(
        ctx.financials, ctx.benchmark, historical=_company_history(ctx),
    )
    ctx.deviations = deviations
    names = ", ".join(d.indicator for d in deviations[:6])
    tracer.milestone("C", "MAD偏差", "success", f"{len(deviations)} 项异常: {names}")
