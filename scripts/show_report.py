"""
==========================================================
 scripts/show_report.py — 在终端格式化显示报告
==========================================================

将已生成的 JSON 格式分析报告以人类可读的方式打印到终端。

支持两种输入：
1. JSON 文件路径：python scripts/show_report.py data/outputs/600519_2024_report.json
2. 标准输入：cat report.json | python scripts/show_report.py

核心功能：
1. 读取 JSON 报告文件（E层输出，符合 Report schema）
2. 格式化输出：公司信息、综合得分、异常清单、多空逻辑栈
3. 适合快速查看分析结果，无需打开 IDE
"""

import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="查看分析报告")
    parser.add_argument("report", nargs="?", help="报告 JSON 文件路径（不传则从 stdin 读取）")
    args = parser.parse_args()

    # 读取报告：文件路径 或 stdin
    if args.report:
        report_path = Path(args.report)
        if not report_path.exists():
            print(f"报告文件不存在: {report_path}")
            return
        with open(report_path, encoding="utf-8") as f:
            report = json.load(f)
    else:
        if sys.stdin.isatty():
            print("用法: python scripts/show_report.py <report.json>")
            print("  或: cat report.json | python scripts/show_report.py")
            return
        report = json.load(sys.stdin)

    _print_report(report)


def _print_report(report: dict):
    """打印格式化报告"""
    sep = "=" * 56

    # 标题
    company = report.get("company_name", "N/A")
    stock = report.get("stock_code", "")
    year = report.get("report_year", "N/A")
    print(f"\n{sep}")
    print(f"  财报分析报告 — {company} ({stock})  {year}年")
    print(f"{sep}")

    # 综合得分
    assessment = report.get("overall_assessment", {})
    if isinstance(assessment, dict):
        score = assessment.get("score", "N/A")
        tier = assessment.get("confidence_tier", "N/A")
        peers = assessment.get("peer_comparisons") or assessment.get("peer_comparison", [])
        peer_text = f" (vs {len(peers)}家同行)" if peers else ""
        print(f"\n  综合得分: {score}/100  [{tier}]{peer_text}")
    else:
        print(f"\n  综合得分: {assessment}")

    # 得分明细（如果有）
    breakdown = report.get("score_breakdown")
    if breakdown:
        print(f"    基础分: {breakdown.get('base_score', 100)}")
        total_ded = breakdown.get("total_deduction", 0)
        details = breakdown.get("anomaly_details", [])
        print(f"    总扣分: {total_ded:.2f} ({len(details)}个异常)")
        if details:
            for d in details[:5]:  # 最多显示5个
                src = d.get("source", "")
                ind = d.get("indicator", "")
                k = d.get("k_value", 0)
                wp = d.get("w_phe", 0)
                as_ = d.get("anomaly_score", 0)
                print(f"      [{src}] {ind}: K={k:.2f}, W={wp:.2f}, score={as_:.2f}")

    # 核心异常
    anomalies = report.get("core_anomalies", [])
    if anomalies:
        print(f"\n  [异常清单] ({len(anomalies)}项)")
        for i, a in enumerate(anomalies, 1):
            indicator = a.get("indicator", "未知指标")
            source = a.get("source", "")
            severity = a.get("severity", "")
            tag = "[严重]" if severity == "extreme" else "[异常]"
            probs = a.get("probabilities", {})
            prob_str = ", ".join(f"{k}: {v*100 if isinstance(v, float) and v<1 else v}%" for k, v in probs.items()) if probs else ""

            print(f"    {i}. {tag} {indicator} ({source})")
            if prob_str:
                print(f"       概率归因: {prob_str}")

            # 证据来源
            evidence = a.get("evidence", [])
            if evidence:
                for e in evidence[:2]:
                    page = e.get("page_number") or e.get("page", "")
                    excerpt = e.get("source_excerpt") or e.get("source", "")
                    if page:
                        print(f"       来源: 第{page}页")
                    if excerpt:
                        print(f"       依据: {excerpt[:80]}...")
    else:
        print(f"\n  [异常清单] 无")

    # 多空逻辑栈
    bull_points = report.get("bull_points", [])
    bear_points = report.get("bear_points", [])
    if bull_points:
        print(f"\n  [看多支持]")
        for p in bull_points:
            print(f"    + {p}")
    if bear_points:
        print(f"\n  [看空风险]")
        for p in bear_points:
            print(f"    - {p}")

    # 报告生成时间
    gen_time = report.get("report_generated_at", "")
    if gen_time:
        print(f"\n{sep}")
        print(f"  生成时间: {gen_time}")

    print(f"{sep}\n")


if __name__ == "__main__":
    main()
