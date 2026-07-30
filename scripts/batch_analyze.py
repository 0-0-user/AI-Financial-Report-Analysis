"""
==========================================================
 scripts/batch_analyze.py — 批量分析多个公司年报
==========================================================

一次性分析多个公司的年报，适合批量处理场景。

核心功能: 
1. 扫描指定目录下所有 PDF 文件
2. 逐个执行完整流水线
3. 所有报告输出到指定目录

使用方式: 
    # 分析某个目录下所有 PDF
    python scripts/batch_analyze.py --pdfs data/raw/ --output data/outputs/
"""

import argparse
from pathlib import Path
from scripts.run_pipeline import run_single_analysis


def main():
    parser = argparse.ArgumentParser(description="批量分析年报")
    parser.add_argument("--pdfs", required=True, help="PDF 文件或目录")
    parser.add_argument("--output", default="data/outputs", help="报告输出目录")
    args = parser.parse_args()

    pdfs_path = Path(args.pdfs)
    if pdfs_path.is_dir():
        pdf_files = list(pdfs_path.glob("*.pdf"))
    else:
        pdf_files = [pdfs_path]

    print(f"📋 共 {len(pdf_files)} 份年报待分析")
    for i, pdf in enumerate(pdf_files, 1):
        print(f"\n[{i}/{len(pdf_files)}] 分析: {pdf.name}")
        # TODO: 调用 run_single_analysis(pdf, args.output)

    print(f"\n✅ 批量分析完成")


if __name__ == "__main__":
    main()
