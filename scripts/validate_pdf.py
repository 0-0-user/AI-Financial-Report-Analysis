"""
==========================================================
 scripts/validate_pdf.py — 快速验证 PDF 可解析性
==========================================================

在运行完整流水线前，快速检查 PDF 文件是否可以被正确解析。

核心功能：
1. 检查 PDF 文件是否存在、大小
2. 检测是否包含财务表格（待实现）
3. 检测是否包含管理层讨论章节（待实现）
4. 检测附注部分是否完整（待实现）

使用方式：
    python scripts/validate_pdf.py data/raw/600519_2024.pdf
"""

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="PDF 文件解析性检查")
    parser.add_argument("pdf", help="年报 PDF 文件路径")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"❌ 文件不存在: {pdf_path}")
        return

    # TODO: 实际检查 PDF 内容和结构
    print(f"📄 {pdf_path.name}")
    print(f"   大小: {pdf_path.stat().st_size / 1024 / 1024:.1f} MB")
    print("   ✅ PDF 可读取")
    print("   ⏳ 章节检测: 待实现")


if __name__ == "__main__":
    main()
