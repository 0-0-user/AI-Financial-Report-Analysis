"""
==========================================================
 scripts/validate_pdf.py — 快速验证 PDF 可解析性
==========================================================

在运行完整流水线前，快速检查 PDF 文件是否可以被正确解析。

检查项目: 
1. 文件是否存在、大小是否合理
2. 是否能读取页数
3. 是否包含年报关键章节 (财务数据、管理层讨论、附注) 
4. 是否包含表格数据

使用方式: 
    python scripts/validate_pdf.py data/raw/600519_2024.pdf
"""

import argparse
import re
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="PDF 文件解析性检查")
    parser.add_argument("pdf", help="年报 PDF 文件路径")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"文件不存在: {pdf_path}")
        return

    checks = []
    errors = []

    # 检查1: 文件大小
    size_mb = pdf_path.stat().st_size / (1024 * 1024)
    if size_mb < 0.5:
        errors.append(f"文件过小 ({size_mb:.1f} MB)，可能不是完整年报")
    elif size_mb > 200:
        errors.append(f"文件过大 ({size_mb:.1f} MB)，可能包含非文本内容")
    else:
        checks.append(f"文件大小 {size_mb:.1f} MB，合理")

    # 检查2: PDF 页数
    page_count = _count_pages(pdf_path)
    if page_count is not None:
        if page_count < 30:
            errors.append(f"页数过少 ({page_count} 页)，可能不是完整年报")
        elif page_count > 1000:
            errors.append(f"页数过多 ({page_count} 页)，可能包含过多附件")
        else:
            checks.append(f"共 {page_count} 页，符合年报页数范围")
    else:
        errors.append("无法读取 PDF 页数，文件可能损坏")

    # 检查3: 提取文本并检测关键章节
    year = _detect_report_year(pdf_path.name)
    if year:
        checks.append(f"文件名包含年份: {year}")

    text_snippets = _extract_text_preview(pdf_path)
    if text_snippets:
        section_checks = _check_sections(text_snippets)
        for section, found in section_checks.items():
            if found:
                checks.append(f"章节检测通过: {section}")
            else:
                errors.append(f"未检测到章节: {section}")

        # 检查表格 (通过搜索常见财务指标) 
        table_keywords = ["营业收入", "净利润", "资产总计", "负债合计", "经营活动", "每股收益"]
        found_keywords = [kw for kw in table_keywords if kw in text_snippets]
        if len(found_keywords) >= 3:
            checks.append(f"财务表格检测通过 (发现 {len(found_keywords)} 项指标) ")
        else:
            errors.append("未检测到足够财务指标，可能不含表格数据")
    else:
        errors.append("无法提取 PDF 文本内容，文件可能为扫描件或加密")

    # 输出结果
    print(f"\n{ pdf_path.name }")
    for c in checks:
        print(f"  [OK] {c}")
    for e in errors:
        print(f"  [FAIL] {e}")

    if errors:
        print(f"\n共 {len(errors)} 项异常，建议人工核验后再运行流水线")
    else:
        print(f"\n全部检查通过，可以运行流水线")


def _count_pages(pdf_path: Path) -> int | None:
    """尝试读取 PDF 页数"""
    try:
        import pdfplumber
        with pdfplumber.open(str(pdf_path)) as pdf:
            return len(pdf.pages)
    except Exception:
        pass

    # 备用方案: 直接读取 PDF 文件头中的 /Pages 信息
    try:
        with open(pdf_path, "rb") as f:
            content = f.read(100_000)  # 只读前 100KB
            matches = re.findall(rb"/Type\s*/Page[^s]", content)
            if matches:
                return len(matches)
    except Exception:
        pass

    return None


def _detect_report_year(filename: str) -> int | None:
    """从文件名检测年报年份"""
    matches = re.findall(r"(20\d{2})", filename)
    if matches:
        return int(matches[0])
    return None


def _extract_text_preview(pdf_path: Path) -> str | None:
    """提取 PDF 前几页的文本做内容检测"""
    try:
        import pdfplumber
        with pdfplumber.open(str(pdf_path)) as pdf:
            texts = []
            for page in pdf.pages[:20]:  # 只看前 20 页
                text = page.extract_text()
                if text:
                    texts.append(text)
            return "\n".join(texts) if texts else None
    except Exception:
        return None


def _check_sections(text: str) -> dict[str, bool]:
    """检测年报关键章节是否存在"""
    sections = {
        "财务数据": ["资产负债表", "利润表", "现金流量表", "营业收入", "净利润"],
        "管理层讨论": ["管理层讨论与分析", "经营情况讨论", "业务回顾", "董事会报告"],
        "附注明细": ["附注", "财务报表附注", "会计政策"],
        "公司概况": ["公司简介", "基本情况", "公司信息"],
    }

    result = {}
    for section_name, keywords in sections.items():
        result[section_name] = any(kw in text for kw in keywords)
    return result


if __name__ == "__main__":
    main()
