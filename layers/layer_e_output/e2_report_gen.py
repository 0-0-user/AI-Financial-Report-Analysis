"""E2层：生成结构化报告

接收已填好所有数据的 PipelineContext，组装最终的 Report 对象。

四个输出模块：
1. 整体研判：综合得分 + 同行横向对比
2. 核心异常与概率归因：异常指标 + 概率分布 + 判断依据
3. 多空逻辑栈：看多支持点 + 看空风险点（文本部分可调 LLM 润色）

使用方式：
    report = generate_report(ctx)
    print(report.model_dump_json(indent=2, ensure_ascii=False))
"""

from datetime import datetime
from pipeline.context import PipelineContext
from schemas.report import (
    Report, OverallAssessment, CoreAnomaly, AnomalyEvidence,
    PeerComparison, ScoreBreakdown,
)
from schemas.reasoning import ProbabilityAssignment


def generate_report(ctx: PipelineContext) -> Report:
    """组装最终的结构化报告"""
    overall = _build_overview(ctx)
    anomalies = _build_anomalies(ctx)
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


def _build_overview(ctx: PipelineContext) -> OverallAssessment:
    """模块1：整体研判"""
    score = ctx.score.final_score if ctx.score else 0
    tier = "高置信度" if score >= 80 else "中等置信度" if score >= 60 else "低置信度"

    peers: list[PeerComparison] = []
    if ctx.benchmark:
        peers = [
            PeerComparison(company_name=p.name, similarity_score=p.similarity_score)
            for p in ctx.benchmark.peer_companies
        ]

    return OverallAssessment(
        score=score,
        confidence_tier=tier,
        peer_comparisons=peers,
    )


def _build_anomalies(ctx: PipelineContext) -> list[CoreAnomaly]:
    """模块2：核心异常与概率归因"""
    anomalies: list[CoreAnomaly] = []

    if ctx.reasoning_results:
        for result in ctx.reasoning_results:
            evidence = []
            for lookup in result.lookups[:3]:  # 最多取3条证据
                evidence.append(AnomalyEvidence(
                    source_excerpt=lookup.source_text[:200],
                    page_number=lookup.page_number,
                ))

            anomalies.append(CoreAnomaly(
                indicator=result.anomaly_indicator,
                source=result.anomaly_source,
                probabilities=result.probabilities,
                evidence=evidence,
            ))

    return anomalies


def _build_bull_bear(ctx: PipelineContext) -> tuple[list[str], list[str]]:
    """模块3：多空逻辑栈"""
    bull: list[str] = []
    bear: list[str] = []

    # 看空点：C层偏差异常
    if ctx.deviations:
        for dev in ctx.deviations:
            if dev.severity == "extreme":
                bear.append(f"{dev.indicator}严重偏离同行（{dev.mad_multiple:.1f}倍MAD）")
            else:
                bear.append(f"{dev.indicator}偏离同行（{dev.mad_multiple:.1f}倍MAD）")

    # 看空点：B+层逻辑异常
    if ctx.logic_anomalies:
        for anomaly in ctx.logic_anomalies:
            bear.append(f"逻辑异常：{anomaly.summary}")

    # 看多点：同行对比
    if ctx.benchmark and len(ctx.benchmark.peer_companies) > 0:
        bull.append(f"在{len(ctx.benchmark.peer_companies)}家可比公司中具备参考价值")

    # 如果既无看多也无看空，给默认值
    if not bull and not bear:
        bull.append("无显著异常指标")
        bear.append("建议进一步分析")

    return bull, bear
