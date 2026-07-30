"""
==========================================================
 scripts/show_report.py — 在终端显示 Markdown 格式分析报告
==========================================================

将已生成的 JSON 格式分析报告渲染为 Markdown 并输出到终端。

支持两种输入: 
1. JSON 文件路径: python scripts/show_report.py data/outputs/600519_2024_report.json
2. 标准输入: cat report.json | python scripts/show_report.py
"""

import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="查看分析报告")
    parser.add_argument("report", nargs="?", help="报告 JSON 文件路径 (不传则从 stdin 读取) ")
    parser.add_argument("--markdown", action="store_true", help="输出 Markdown 原始格式")
    args = parser.parse_args()

    # 读取报告: 文件路径 或 stdin
    if args.report:
        report_path = Path(args.report)
        if not report_path.exists():
            print(f"报告文件不存在: {report_path}")
            return
        with open(report_path, encoding="utf-8") as f:
            report_data = json.load(f)
    else:
        if sys.stdin.isatty():
            print("用法: python scripts/show_report.py <report.json>")
            print("  或: cat report.json | python scripts/show_report.py")
            return
        report_data = json.load(sys.stdin)

    if args.markdown:
        print(_render_markdown_raw(report_data))
    else:
        _print_terminal(report_data)


def _render_markdown_raw(report: dict) -> str:
    """将 report dict 渲染为 Markdown (轻量版本，不依赖 E2 层) """
    lines = []
    company = report.get("company_name", "N/A")
    stock = report.get("stock_code", "")
    year = report.get("report_year", "N/A")

    lines.append(f"# 财报分析报告 — {company} ({stock}) {year}年")
    lines.append("")
    lines.append(f"> 生成时间: {report.get('report_generated_at', '')}")
    lines.append("")

    # 模块1: 综合评级
    assessment = report.get("overall_assessment", {}) or {}
    if isinstance(assessment, dict):
        score = assessment.get("score", "N/A")
        tier = assessment.get("confidence_tier", "N/A")
        lines.append("## 🏆 综合评级")
        lines.append("")
        lines.append(f"**置信度得分**: {score} / 100")
        lines.append(f"**评级**: {tier}")

        ti = report.get("trust_interval")
        if ti:
            lines.append(f"**不确定性区间**: [{ti.get('lower_bound', '?')}, {ti.get('upper_bound', '?')}]")
            lines.append(f"- 平均 m(Θ) = {ti.get('avg_m_theta', 0)}")
            lines.append(f"- 平均 K = {ti.get('avg_k', 0)}")
        lines.append("")

        peers = assessment.get("peer_comparisons") or assessment.get("peer_comparison", [])
        if peers:
            lines.append("**同行对比**: ")
            for p in peers[:5]:
                lines.append(f"- {p.get('company_name', '')} (相似度: {p.get('similarity_score', 0):.2f}) ")
            lines.append("")

    # 评分明细
    breakdown = report.get("score_breakdown")
    if breakdown and isinstance(breakdown, dict):
        details = breakdown.get("anomaly_details", [])
        if details:
            lines.append("**评分明细**: ")
            lines.append("")
            lines.append("| 指标 | 来源 | W_phe | K值 | 中心扣分 | [Bel, Pl] |")
            lines.append("|------|------|-------|-----|---------|-----------|")
            for d in details:
                lines.append(
                    f"| {d.get('indicator', '')} | {d.get('source', '')} | "
                    f"{d.get('w_phe', 0):.2f} | {d.get('k_value', 0):.2f} | "
                    f"{d.get('anomaly_score', 0):.2f} | "
                    f"[{d.get('score_bel', 0):.2f}, {d.get('score_pl', 0):.2f}] |"
                )
            lines.append("")

    lines.append("---")
    lines.append("")

    # 模块2: 核心异常
    anomalies = report.get("core_anomalies", [])
    if anomalies:
        lines.append("## ⚠️ 核心异常指标")
        lines.append("")
        for a in anomalies:
            tag = "🔴 严重" if a.get("severity") == "extreme" else "🟡 异常"
            lines.append(f"### {tag} {a.get('indicator', '未知')} ({a.get('source', '')}层) ")
            lines.append(f"- K值: {a.get('k_value', 0)}")
            probs = a.get("probabilities", {})
            if probs:
                prob_str = "，".join(
                    f"{k}: {v*100 if isinstance(v, float) and v<1 else v}%"
                    for k, v in probs.items()
                )
                lines.append(f"- 概率归因: {prob_str}")
            evidence = a.get("evidence", [])
            for e in evidence[:2]:
                page = e.get("page_number", 0)
                excerpt = e.get("source_excerpt", "")[:100]
                if page:
                    lines.append(f"- 证据来源: 第{page}页")
                if excerpt:
                    lines.append(f"  > {excerpt}")
            lines.append("")

    lines.append("---")
    lines.append("")

    # 模块3: 解释
    expl = report.get("explanation_text")
    if expl:
        lines.append("## 🔍 解释")
        lines.append("")
        lines.append(expl)
        lines.append("")

    lines.append("---")
    lines.append("")

    # 模块4: 总结
    summary = report.get("summary_text")
    if summary:
        lines.append("## 📋 总结报告")
        lines.append("")
        lines.append(summary)
        lines.append("")

    lines.append("---")
    lines.append("")

    # 多空逻辑栈
    bull = report.get("bull_points", [])
    bear = report.get("bear_points", [])
    if bull or bear:
        lines.append("## 📈 多空逻辑栈")
        lines.append("")
        if bull:
            lines.append("**看多支持点**: ")
            for p in bull:
                lines.append(f"- ✅ {p}")
            lines.append("")
        if bear:
            lines.append("**看空风险点**: ")
            for p in bear:
                lines.append(f"- ⚠️ {p}")
        lines.append("")

    lines.append("---")
    lines.append("")

    # 模块5: 证据溯源
    evidence_sources = report.get("evidence_sources", [])
    if evidence_sources:
        lines.append("## 📎 附注: 证据溯源")
        lines.append("")
        for es in evidence_sources:
            lines.append(f"[^{es.get('footnote_id', '')}]: **{es.get('cause', '')}**")
            if es.get("source_type") == "年报原文":
                lines.append(f"    ├─ 年报原文: 第{es.get('page_number', '?')}页——\"{es.get('source_text', '')[:200]}\"")
            else:
                lines.append(f"    └─ {es.get('source_type', '')}: {es.get('source_text', '')[:200]}")
            lines.append("")

    return "\n".join(lines)


def _print_terminal(report: dict):
    """终端友好格式输出"""
    lines = _render_markdown_raw(report)
    print(lines)


if __name__ == "__main__":
    main()
