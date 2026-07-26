"""在终端格式化显示报告

用法：
    python scripts/show_report.py data/outputs/600519_2024_report.json
"""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="查看分析报告")
    parser.add_argument("report", help="报告 JSON 文件路径")
    args = parser.parse_args()

    report_path = Path(args.report)
    if not report_path.exists():
        print(f"❌ 报告文件不存在: {report_path}")
        return

    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    print(f"\n{'='*50}")
    print(f"📊 财报分析报告")
    print(f"{'='*50}")
    print(f"公司: {report.get('company_name', 'N/A')}")
    print(f"年份: {report.get('report_year', 'N/A')}")

    assessment = report.get("overall_assessment", {})
    print(f"\n🏆 综合得分: {assessment.get('score', 'N/A')}")
    print(f"   置信度: {assessment.get('confidence_tier', 'N/A')}")

    print(f"\n⚠️  核心异常:")
    for anomaly in report.get("core_anomalies", []):
        print(f"  - {anomaly.get('indicator')} ({anomaly.get('source')})")

    print(f"\n📈 看多支持点:")
    for point in report.get("bull_points", []):
        print(f"  ✅ {point}")

    print(f"\n📉 看空风险点:")
    for point in report.get("bear_points", []):
        print(f"  ⚠️  {point}")

    print(f"\n{'='*50}\n")


if __name__ == "__main__":
    main()
