"""E2层：生成结构化报告"""

from datetime import datetime
from pipeline.context import PipelineContext
from schemas.report import Report


def generate_report(ctx: PipelineContext) -> Report:
    """组装最终的结构化报告

    四个输出模块：
    1. 整体研判：综合得分 + 同行横向对比
    2. 核心异常与概率归因：异常指标 + 概率分布 + 判断依据
    3. 多空逻辑栈：看多支持点 + 看空风险点
    """
    # 模块1：整体研判
    overall = _build_overview(ctx)

    # 模块2：核心异常
    anomalies = _build_anomalies(ctx)

    # 模块3：多空逻辑栈（文本部分可调用 LLM 润色）
    bull, bear = _build_bull_bear(ctx)

    report = Report(
        company_name=ctx.tags.company_name if ctx.tags else "",
        stock_code=ctx.tags.stock_code if ctx.tags else "",
        report_year=ctx.financials.year if ctx.financials else 0,
        overall_assessment=overall,
        core_anomalies=anomalies,
        bull_points=bull,
        bear_points=bear,
        score_breakdown=ctx.score,
        report_generated_at=datetime.now().isoformat(),
    )

    return report


def _build_overview(ctx: PipelineContext) -> dict:
    score = ctx.score.final_score if ctx.score else 0
    tier = "高置信度" if score >= 80 else "中等置信度" if score >= 60 else "低置信度"

    overview = {
        "score": score,
        "confidence_tier": tier,
        "peer_count": len(ctx.benchmark.peer_companies) if ctx.benchmark else 0,
    }

    if ctx.benchmark:
        overview["peer_comparison"] = [
            {"name": p.name, "similarity": p.similarity_score}
            for p in ctx.benchmark.peer_companies
        ]

    return overview


def _build_anomalies(ctx: PipelineContext) -> list[dict]:
    anomalies = []

    if ctx.reasoning_results:
        for result in ctx.reasoning_results:
            anomaly_entry = {
                "indicator": result.anomaly_indicator,
                "source": result.anomaly_source,
                "probabilities": result.probabilities,
                "evidence": [
                    {"source": e.source_text[:100], "page": e.page_number}
                    for e in result.lookups[:2]
                ],
            }
            anomalies.append(anomaly_entry)

    return anomalies


def _build_bull_bear(ctx: PipelineContext) -> tuple[list[str], list[str]]:
    """提炼多空逻辑栈"""
    bull = []
    bear = []

    if ctx.deviations:
        for dev in ctx.deviations:
            if dev.severity == "extreme":
                bear.append(f"{dev.indicator}严重偏离同行（{dev.mad_multiple:.1f}倍MAD）")

    if ctx.logic_anomalies:
        for anomaly in ctx.logic_anomalies:
            bear.append(f"逻辑异常：{anomaly.summary}")

    if ctx.benchmark and ctx.benchmark.peer_companies:
        bull.append(f"在{len(ctx.benchmark.peer_companies)}家可比公司中具备参考价值")

    return bull, bear
