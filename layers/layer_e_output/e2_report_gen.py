"""E2层：生成结构化报告（6 模块 + Markdown 渲染）

接收已填好所有数据的 PipelineContext，组装最终的 Report 对象，
并渲染为人类可读的 Markdown 文档。

六个输出模块：
0. 宏观事实 — 8 张折线图
1. 综合评级与得分区间 — [Bel, Pl] 信任区间
2. 核心异常指标清单 — B+/C 异常
3. 解释 — D2 概率归因 + K 冲突（LLM 模板填充）
4. 总结报告 — LLM 全量语义分析
5. 证据溯源 — 出处+页码

使用方式：
    report = generate_report(ctx)
    markdown = render_to_markdown(report)
    print(markdown)
"""

import json
import logging
from datetime import datetime
from typing import Optional

from pipeline.context import PipelineContext
from schemas.report import (
    Report, OverallAssessment, CoreAnomaly, AnomalyEvidence,
    PeerComparison, ScoreBreakdown, TrustInterval, EvidenceSource,
    AnomalyScoreDetail, ChartConfig,
)

logger = logging.getLogger(__name__)


def _safe_get_k(reasoning) -> float:
    """安全地从 reasoning 对象提取 K 值，兼容 mock 对象"""
    if not reasoning:
        return 0.0
    try:
        ds_meta = getattr(reasoning, "ds_metadata", None)
        if isinstance(ds_meta, dict):
            return float(ds_meta.get("conflict_K", 0.0))
    except (TypeError, ValueError, AttributeError):
        pass
    return 0.0


def generate_report(ctx: PipelineContext) -> Report:
    """主入口：组装最终的结构化报告"""
    # 模块0：生成图表
    chart_configs = _build_charts(ctx)

    # 模块1：信任区间
    trust_interval = _build_trust_interval(ctx)

    # 模块1：整体研判（复用旧逻辑）
    overall = _build_overview(ctx, trust_interval)

    # 模块2：核心异常清单
    anomalies = _build_core_anomalies(ctx)

    # 模块3：解释（LLM 模板填充，失败则回退到结构化渲染）
    explanation_text = _build_explanations_llm(ctx)

    # 模块4：总结报告（LLM 全量语义分析，失败则回退）
    summary_text = _build_summary_llm(ctx, overall, anomalies, trust_interval)

    # 模块5：证据溯源
    evidence_sources = _build_evidence_sources(ctx)

    # 多空逻辑栈（保留兼容）
    bull, bear = _build_bull_bear(ctx)

    report = Report(
        company_name=ctx.tags.company_name if ctx.tags else "",
        stock_code=ctx.tags.stock_code if ctx.tags else "",
        report_year=ctx.financials.year if ctx.financials else 0,
        chart_configs=chart_configs,
        chart_base_path="charts",
        overall_assessment=overall,
        trust_interval=trust_interval,
        core_anomalies=anomalies,
        explanation_text=explanation_text,
        summary_text=summary_text,
        evidence_sources=evidence_sources,
        score_breakdown=ctx.score,
        bull_points=bull,
        bear_points=bear,
        report_generated_at=datetime.now().isoformat(),
    )
    return report


# ════════════════════════════════════════════
# 模块0：宏观事实（8 张折线图）
# ════════════════════════════════════════════

def _build_charts(ctx: PipelineContext) -> list[ChartConfig]:
    """调用图表引擎生成 8 张 PNG，返回配置列表"""
    if not ctx.multi_year_financials or not ctx.tags:
        logger.warning("无多年财务数据，跳过图表生成")
        return []

    try:
        from .chart_engine import generate_all_charts, CHART_CONFIGS

        chart_results = generate_all_charts(
            multi_year_data=ctx.multi_year_financials,
            company_names=ctx.multi_year_company_names or {},
            target_stock_code=ctx.tags.stock_code,
        )
        # 将 dict 结果转为 ChartConfig schema
        configs = []
        for r in chart_results:
            # 从 CHART_CONFIGS 查找单位
            unit = ""
            for cfg in CHART_CONFIGS:
                if cfg["column"] == r["column"]:
                    unit = cfg.get("unit", "")
                    break
            configs.append(ChartConfig(
                indicator_name=r["name"],
                akshare_column=r["column"],
                filename=r["filename"],
                unit=unit,
            ))
        return configs
    except Exception as e:
        logger.warning(f"图表生成失败（非阻断）: {e}")
        return []


# ════════════════════════════════════════════
# 模块1：信任区间 [Bel, Pl]
# ════════════════════════════════════════════

def _build_trust_interval(ctx: PipelineContext) -> Optional[TrustInterval]:
    """从 E1 评分汇总聚合 Bel/Pl 信任区间"""
    if not ctx.score:
        return None

    score = ctx.score
    details = score.anomaly_details or []

    if not details:
        return TrustInterval(
            center_score=score.final_score,
            lower_bound=score.final_score,
            upper_bound=score.final_score,
            avg_m_theta=0.0,
            avg_k=0.0,
        )

    # 汇总所有异常的 bel/pl
    total_bel = 100.0 + sum(d.score_bel for d in details)
    total_pl = 100.0 + sum(d.score_pl for d in details)
    total_center = 100.0 + sum(d.anomaly_score for d in details)

    # 平均 m(Θ) 和 K
    m_theta_sum = 0.0
    k_sum = 0.0
    for d in details:
        k_sum += d.k_value
        # 从 mass_final 读取 m(Θ) 的近似值：
        # 如果 bel != pl → 有不确定性 → m_θ > 0
        # 简单估计：mθ ≈ |score_pl - score_bel| / (2 * |w_phe * delta|)...
        # 实际上我们用更简单的方式：mθ ≈ (d.score_pl - d.anomaly_score) / (w_phe * delta * 5)
        # 但这个太 hacky 了。从异常分数反推 mθ 不准确。
        # 对于展示，我们用 bel/pl 的相对差异来表示不确定性层级。
        m_theta_sum += _estimate_m_theta(d)

    count = len(details)
    return TrustInterval(
        center_score=round(total_center, 2),
        lower_bound=round(min(total_bel, total_center), 2),
        upper_bound=round(max(total_pl, total_center), 2),
        avg_m_theta=round(m_theta_sum / count, 4) if count > 0 else 0.0,
        avg_k=round(k_sum / count, 4) if count > 0 else 0.0,
    )


def _estimate_m_theta(detail: AnomalyScoreDetail) -> float:
    """从 anomaly score 的 bel/pl 差异估算 m(Θ)"""
    diff = abs(detail.score_pl - detail.score_bel)
    if diff < 1e-6 or abs(detail.w_phe * detail.delta) < 1e-6:
        return 0.0
    # diff ≈ 2 × w_phe × delta × m_θ × 5 / known
    # 反推 m_θ ≈ diff / (2 * w_phe * delta * 5)
    estimated = diff / (2 * detail.w_phe * detail.delta * 5)
    return min(max(estimated, 0.0), 1.0)


def _build_overview(
    ctx: PipelineContext,
    trust_interval: Optional[TrustInterval],
) -> OverallAssessment:
    """模块1 上半部分：综合评级"""
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


# ════════════════════════════════════════════
# 模块2：核心异常指标清单（增强版）
# ════════════════════════════════════════════

def _build_core_anomalies(ctx: PipelineContext) -> list[CoreAnomaly]:
    """从 B+/C/D 层数据构建增强的异常清单"""
    anomalies: list[CoreAnomaly] = []
    reasoning_map = _build_reasoning_map(ctx)

    # B+ 层异常
    if ctx.logic_anomalies:
        for anomaly in ctx.logic_anomalies:
            reasoning = reasoning_map.get(("B+", anomaly.check_name))
            k_value = _safe_get_k(reasoning)
            anomalies.append(_make_core_anomaly(
                indicator=anomaly.check_name,
                source="B+",
                probabilities=getattr(reasoning, "probabilities", None) if reasoning else None,
                evidence=_get_lookup_evidence(reasoning) if reasoning else [],
                severity=_severity_label(anomaly.severity),
                k_value=k_value,
                anomaly_score_detail=None,  # 由 E1 提供
            ))

    # C 层偏差异常
    if ctx.deviations:
        for dev in ctx.deviations:
            reasoning = reasoning_map.get(("C", dev.indicator))
            k_value = _safe_get_k(reasoning)
            anomalies.append(_make_core_anomaly(
                indicator=dev.indicator,
                source="C",
                probabilities=getattr(reasoning, "probabilities", None) if reasoning else None,
                evidence=_get_lookup_evidence(reasoning) if reasoning else [],
                severity=dev.severity,
                k_value=k_value,
                anomaly_score_detail=None,
            ))

    return anomalies


def _build_reasoning_map(ctx: PipelineContext) -> dict:
    """构建 { (source, indicator): reasoning_result } 索引"""
    mapping = {}
    if ctx.reasoning_results:
        for rr in ctx.reasoning_results:
            mapping[(rr.anomaly_source, rr.anomaly_indicator)] = rr
    return mapping


def _make_core_anomaly(
    indicator: str, source: str,
    probabilities: Optional[dict],
    evidence: list[AnomalyEvidence],
    severity: str,
    k_value: float,
    anomaly_score_detail: Optional[AnomalyScoreDetail],
) -> CoreAnomaly:
    return CoreAnomaly(
        indicator=indicator,
        source=source,
        probabilities=probabilities or {},
        evidence=evidence,
        severity=severity,
        k_value=k_value,
        anomaly_score_detail=anomaly_score_detail,
    )


def _get_lookup_evidence(reasoning) -> list[AnomalyEvidence]:
    """从 D1 路1结果提取证据"""
    evidence = []
    for lookup in getattr(reasoning, "lookups", [])[:3]:
        evidence.append(AnomalyEvidence(
            source_excerpt=lookup.source_text[:200] if hasattr(lookup, "source_text") else str(lookup)[:200],
            page_number=getattr(lookup, "page_number", 0),
            source_type="年报原文",
        ))
    return evidence


def _severity_label(severity) -> str:
    if isinstance(severity, str):
        return severity
    if isinstance(severity, (int, float)):
        return "extreme" if severity >= 4.0 else "abnormal"
    return "abnormal"


# ════════════════════════════════════════════
# 模块3：解释（LLM 模板格式化）
# ════════════════════════════════════════════

def _build_explanations_llm(ctx: PipelineContext) -> Optional[str]:
    """LLM 按模板格式化异常解释文本

    LLM 失败则回退到模板渲染（非阻断）。
    """
    if not ctx.reasoning_results:
        return None

    # 构建 LLM 输入数据结构
    anomalies_data = []
    footnote_idx = 0
    for rr in ctx.reasoning_results:
        ds_meta = rr.ds_metadata or {}
        k_val = ds_meta.get("conflict_K", 0.0)

        causes = []
        for cause, prob in (rr.probabilities or {}).items():
            footnote_idx += 1
            footnote_id = f"fn_{footnote_idx:03d}"

            # 查找对应的路1证据
            lookup_text = ""
            lookup_page = 0
            if rr.lookups:
                for lk in rr.lookups:
                    if hasattr(lk, "summary") and (lk.summary in cause or cause in lk.summary):
                        lookup_text = getattr(lk, "source_text", "")[:200]
                        lookup_page = getattr(lk, "page_number", 0)
                        break
                if not lookup_text and rr.lookups:
                    lk = rr.lookups[0]
                    lookup_text = getattr(lk, "source_text", "")[:200]
                    lookup_page = getattr(lk, "page_number", 0)

            # 查找对应的路2假设
            hypo_text = ""
            hypo_source = ""
            if rr.hypotheses:
                for h in rr.hypotheses:
                    if hasattr(h, "hypothesis") and (h.hypothesis in cause or cause in h.hypothesis):
                        hypo_text = getattr(h, "reasoning", "")[:200]
                        hypo_source = getattr(h, "source", "")
                        break
                if not hypo_text and rr.hypotheses:
                    h = rr.hypotheses[0]
                    hypo_text = getattr(h, "reasoning", "")[:200]
                    hypo_source = getattr(h, "source", "")

            causes.append({
                "cause": cause,
                "probability": round(prob * 100, 1),
                "footnote_id": footnote_id,
                "lookup_text": lookup_text,
                "lookup_page": lookup_page,
                "hypo_text": hypo_text,
                "hypo_source": hypo_source,
            })

        anomalies_data.append({
            "indicator": rr.anomaly_indicator,
            "source": rr.anomaly_source,
            "k_value": round(k_val, 4),
            "causes": causes,
        })

    # 调用 LLM
    try:
        from llm.client import LLMClient
        client = LLMClient()
        response = client.chat(
            "e2_explanation",
            {"anomalies_json": json.dumps(anomalies_data, ensure_ascii=False, indent=2)},
        )
        if response and str(response).strip():
            return str(response)
    except Exception as e:
        logger.warning(f"E2 模块3 LLM 调用失败（使用回退模板）: {e}")

    # 回退：结构化渲染
    return _render_explanation_fallback(anomalies_data)


def _render_explanation_fallback(anomalies_data: list[dict]) -> str:
    """LLM 失败时的回退：纯模板渲染"""
    lines = []
    for item in anomalies_data:
        lines.append(f"### {item['indicator']}（{item['source']}层）")
        k = item["k_value"]
        if k > 0.6:
            lines.append(f"> 证据冲突系数 K = {k}（两路证据存在显著分歧）")
        else:
            lines.append(f"> 证据冲突系数 K = {k}（证据一致性较高）")
        lines.append("")
        lines.append("原因分布：")
        for c in item["causes"]:
            lines.append(f"- {c['cause']}：{c['probability']}%")
            if c["lookup_text"]:
                lines.append(f"  - 年报原文：{c['lookup_text'][:100]}（第{c['lookup_page']}页）[^{c['footnote_id']}]")
            if c["hypo_text"]:
                lines.append(f"  - 推演假设：{c['hypo_text'][:100]}（来源：{c['hypo_source']}）")
        lines.append("")
    return "\n".join(lines)


# ════════════════════════════════════════════
# 模块4：总结报告（LLM 全量语义分析）
# ════════════════════════════════════════════

def _build_summary_llm(
    ctx: PipelineContext,
    overall: OverallAssessment,
    anomalies: list[CoreAnomaly],
    trust_interval: Optional[TrustInterval],
) -> Optional[str]:
    """LLM 生成纲领性总结

    LLM 失败则回退到简单的结构化摘要。
    """
    if not ctx.score:
        return None

    score = ctx.score
    top_anomalies = []
    for a in anomalies[:5]:
        semantic_scores_avg = 0
        if a.anomaly_score_detail and a.anomaly_score_detail.causes:
            scores = [c.score_base for c in a.anomaly_score_detail.causes]
            semantic_scores_avg = sum(scores) / len(scores) if scores else 0
        top_anomalies.append({
            "source": a.source,
            "indicator": a.indicator,
            "k_value": a.k_value,
            "semantic_score": semantic_scores_avg,
        })

    avg_u = trust_interval.avg_m_theta if trust_interval else 0.0
    bel_s = trust_interval.lower_bound if trust_interval else score.final_score
    pl_s = trust_interval.upper_bound if trust_interval else score.final_score

    # 汇总结构化数据
    summary_data = {
        "异常总数": len(anomalies),
        "总扣分": score.total_deduction,
        "评分明细": [
            {"指标": d.indicator, "来源": d.source, "扣分": d.anomaly_score,
             "K值": d.k_value, "Bel": d.score_bel, "Pl": d.score_pl}
            for d in (score.anomaly_details or [])
        ],
    }

    try:
        from llm.client import LLMClient
        client = LLMClient()
        response = client.chat(
            "e2_summary",
            {
                "company_name": ctx.tags.company_name if ctx.tags else "",
                "stock_code": ctx.tags.stock_code if ctx.tags else "",
                "report_year": ctx.financials.year if ctx.financials else 0,
                "center_score": score.final_score,
                "bel_score": bel_s,
                "pl_score": pl_s,
                "avg_uncertainty": avg_u,
                "anomaly_count": len(anomalies),
                "top_anomalies": json.dumps(top_anomalies, ensure_ascii=False),
                "summary_data": json.dumps(summary_data, ensure_ascii=False, indent=2),
            },
        )
        if response and str(response).strip():
            return str(response)
    except Exception as e:
        logger.warning(f"E2 模块4 LLM 调用失败（使用回退模板）: {e}")

    # 回退：简单总结
    return _render_summary_fallback(
        score, overall, trust_interval, anomalies,
    )


def _render_summary_fallback(
    score: ScoreBreakdown,
    overall: OverallAssessment,
    trust_interval: Optional[TrustInterval],
    anomalies: list[CoreAnomaly],
) -> str:
    """LLM 失败时的回退：结构化摘要"""
    lines = [
        "## 总结报告（结构化摘要）",
        "",
        f"综合得分 {score.final_score} / 100，{overall.confidence_tier}。",
        "",
        f"共发现 {len(anomalies)} 项异常：",
    ]
    for a in anomalies:
        lines.append(f"- [{a.source}] {a.indicator}（K={a.k_value}）")
    if trust_interval:
        lines.append("")
        lines.append(f"不确定性区间：[{trust_interval.lower_bound}, {trust_interval.upper_bound}]")
        lines.append(f"平均 m(Θ)={trust_interval.avg_m_theta}，平均 K={trust_interval.avg_k}")
    return "\n".join(lines)


# ════════════════════════════════════════════
# 模块5：证据溯源
# ════════════════════════════════════════════

def _build_evidence_sources(ctx: PipelineContext) -> list[EvidenceSource]:
    """从 D1/D2 层数据构建证据溯源列表"""
    sources: list[EvidenceSource] = []
    footnote_idx = 0

    if not ctx.reasoning_results:
        return sources

    for rr in ctx.reasoning_results:
        for cause in (rr.probabilities or {}).keys():
            footnote_idx += 1
            footnote_id = f"fn_{footnote_idx:03d}"

            # 路1证据
            found_lookup = False
            if rr.lookups:
                for lk in rr.lookups:
                    summary = getattr(lk, "summary", "")
                    if summary and (summary in cause or cause in summary):
                        sources.append(EvidenceSource(
                            footnote_id=footnote_id,
                            cause=cause,
                            source_type="年报原文",
                            source_text=getattr(lk, "source_text", "")[:300],
                            page_number=getattr(lk, "page_number", None),
                        ))
                        found_lookup = True
                        break

            # 路2假设（如果路1没找到匹配）
            if not found_lookup and rr.hypotheses:
                for h in rr.hypotheses:
                    hypo = getattr(h, "hypothesis", "")
                    if hypo and (hypo in cause or cause in hypo):
                        sources.append(EvidenceSource(
                            footnote_id=footnote_id,
                            cause=cause,
                            source_type="推演假设",
                            source_text=getattr(h, "reasoning", "")[:300],
                            hypothesis_reasoning=getattr(h, "reasoning", "")[:300],
                        ))
                        found_lookup = True
                        break

            # 保底
            if not found_lookup:
                sources.append(EvidenceSource(
                    footnote_id=footnote_id,
                    cause=cause,
                    source_type="综合分析",
                    source_text="D-S 证据理论合成结果",
                ))

    return sources


# ════════════════════════════════════════════
# 多空逻辑栈（保留兼容）
# ════════════════════════════════════════════

def _build_bull_bear(ctx: PipelineContext) -> tuple[list[str], list[str]]:
    """构建多空逻辑栈（复用旧逻辑）"""
    bull: list[str] = []
    bear: list[str] = []

    if ctx.deviations:
        for dev in ctx.deviations:
            if dev.severity == "extreme":
                bear.append(f"{dev.indicator}严重偏离同行（{dev.mad_multiple:.1f}倍MAD）")
            else:
                bear.append(f"{dev.indicator}偏离同行（{dev.mad_multiple:.1f}倍MAD）")

    if ctx.logic_anomalies:
        for anomaly in ctx.logic_anomalies:
            bear.append(f"逻辑异常：{anomaly.summary}")

    if ctx.benchmark and len(ctx.benchmark.peer_companies) > 0:
        bull.append(f"在{len(ctx.benchmark.peer_companies)}家可比公司中具备参考价值")

    if not bull and not bear:
        bull.append("无显著异常指标")
        bear.append("建议进一步分析")

    return bull, bear


# ════════════════════════════════════════════
# Markdown 渲染
# ════════════════════════════════════════════

def render_to_markdown(report: Report) -> str:
    """将结构化 Report 渲染为 Markdown 文档"""
    lines = []

    # ── 标题 ──
    company = report.company_name or "N/A"
    stock = report.stock_code or ""
    year = report.report_year or "N/A"
    lines.append(f"# 财报分析报告 — {company}（{stock}）{year}年")
    lines.append("")
    lines.append(f"> 生成时间：{report.report_generated_at}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── 模块0：宏观事实 ──
    lines.append("## 📊 模块0：宏观事实")
    lines.append("")
    if report.chart_configs:
        for cfg in report.chart_configs:
            img_path = f"{report.chart_base_path}/{cfg.filename}"
            alt_text = cfg.indicator_name
            lines.append(f"![{alt_text}]({img_path})")
            lines.append("")
    else:
        lines.append("*（无可用的图表数据）*")
        lines.append("")

    lines.append("---")
    lines.append("")

    # ── 模块1：综合评级与得分区间 ──
    lines.append("## 🏆 模块1：综合评级与得分区间")
    lines.append("")
    assessment = report.overall_assessment
    lines.append(f"**置信度得分**：{assessment.score} / 100")
    if report.trust_interval:
        ti = report.trust_interval
        lines.append(f"**不确定性区间**：[{ti.lower_bound}, {ti.upper_bound}]")
        lines.append(f"**评级**：{assessment.confidence_tier}")
        lines.append(f"")
        lines.append(f"- 平均 m(Θ)（未分配不确定性）={ti.avg_m_theta}")
        lines.append(f"- 平均 K（证据冲突系数）={ti.avg_k}")
    else:
        lines.append(f"**评级**：{assessment.confidence_tier}")

    if assessment.peer_comparisons:
        lines.append("")
        lines.append("**同行对比**：")
        for p in assessment.peer_comparisons[:5]:
            lines.append(f"- {p.company_name}（相似度：{p.similarity_score:.2f}）")
    lines.append("")

    # 评分明细表
    if report.score_breakdown and report.score_breakdown.anomaly_details:
        lines.append("**评分明细**：")
        lines.append("")
        lines.append("| 指标 | 来源 | W_phe | K值 | 中心扣分 | [Bel, Pl] |")
        lines.append("|------|------|-------|-----|---------|-----------|")
        for d in report.score_breakdown.anomaly_details:
            lines.append(
                f"| {d.indicator} | {d.source} | {d.w_phe:.2f} | "
                f"{d.k_value:.2f} | {d.anomaly_score:.2f} | "
                f"[{d.score_bel:.2f}, {d.score_pl:.2f}] |"
            )
        lines.append("")

    lines.append("---")
    lines.append("")

    # ── 模块2：核心异常指标清单 ──
    lines.append("## ⚠️ 模块2：核心异常指标清单")
    lines.append("")
    if report.core_anomalies:
        for a in report.core_anomalies:
            tag = "🔴 严重" if a.severity == "extreme" else "🟡 异常"
            lines.append(f"### {tag} {a.indicator}（{a.source}层）")
            lines.append(f"- K值（证据冲突）：{a.k_value}")
            if a.probabilities:
                prob_str = "，".join(f"{k}：{v*100 if v<1 else v}%" for k, v in a.probabilities.items())
                lines.append(f"- 概率归因：{prob_str}")
            if a.evidence:
                for e in a.evidence[:2]:
                    if e.page_number:
                        lines.append(f"- 证据来源：第{e.page_number}页")
                    if e.source_excerpt:
                        lines.append(f"  > {e.source_excerpt[:100]}")
            lines.append("")
    else:
        lines.append("*无异常指标*")
        lines.append("")

    lines.append("---")
    lines.append("")

    # ── 模块3：解释 ──
    lines.append("## 🔍 模块3：解释")
    lines.append("")
    if report.explanation_text:
        lines.append(report.explanation_text)
    else:
        lines.append("*（无解释信息）*")
    lines.append("")

    lines.append("---")
    lines.append("")

    # ── 模块4：总结报告 ──
    lines.append("## 📋 模块4：总结报告")
    lines.append("")
    if report.summary_text:
        lines.append(report.summary_text)
    else:
        lines.append("*（无总结信息）*")
    lines.append("")

    lines.append("---")
    lines.append("")

    # ── 多空逻辑栈（兼容） ──
    if report.bull_points or report.bear_points:
        lines.append("## 📈 多空逻辑栈")
        lines.append("")
        if report.bull_points:
            lines.append("**看多支持点**：")
            for p in report.bull_points:
                lines.append(f"- ✅ {p}")
            lines.append("")
        if report.bear_points:
            lines.append("**看空风险点**：")
            for p in report.bear_points:
                lines.append(f"- ⚠️ {p}")
        lines.append("")
        lines.append("---")
        lines.append("")

    # ── 模块5：附注·证据溯源 ──
    if report.evidence_sources:
        lines.append("## 📎 模块5：附注·证据溯源")
        lines.append("")
        for es in report.evidence_sources:
            lines.append(f"[^{es.footnote_id}]: **{es.cause}**")
            if es.source_type == "年报原文":
                lines.append(f"    ├─ 年报原文：第{es.page_number}页——\"{es.source_text[:200]}\"")
            else:
                lines.append(f"    └─ {es.source_type}：{es.source_text[:200]}")
                if es.hypothesis_reasoning:
                    lines.append(f"        └─ 推理逻辑：{es.hypothesis_reasoning[:200]}")
            lines.append("")

    return "\n".join(lines)
