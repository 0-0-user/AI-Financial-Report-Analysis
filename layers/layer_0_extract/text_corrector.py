"""第0层: OCR 文本纠错器 — 修复 PDF 扫描件中常见的识别错误

适用范围: 中文财务文本的 OCR 后处理

常见错误类型: 
1. 形近字: 资釐->资金、负偾->负债、收亼->收入
2. 数字混淆: 0↔O↔o、1↔l↔I、5↔S、7↔T
3. 漏字/多字: 合并报表->合报表、资产负债->资产负债资
4. 标点/空格: 被 OCR 误加或遗漏的标点

设计约束: 
- 在 chunker 切分后、merger 合并前运行
- 只修正明显的 OCR 错误，不做语义修改
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── 形近字纠错: dict + 编译正则 -> 单次扫描 O(text_length) ──
_CORRECTION_MAP: dict[str, str] = {
    "资釐": "资金", "负偾": "负债", "负渍": "负债", "收亼": "收入",
    "攴出": "支出", "资卢": "资产", "权盉": "权益", "利闰": "利润",
    "朿用": "费用", "折|日": "折旧", "摊铕": "摊销", "税釐": "税金",
    "应攵": "应收", "应付款": "应付", "呆账": "坏账", "存贷": "存货",
    "固走": "固定", "无刑": "无形", "商雀": "商誉", "股朏": "股东",
    "嘌据": "票据", "借欯": "借款", "利恿": "利息", "汇兑": "汇兑",
    "损盉": "损益", "综台": "综合", "经喾": "经营", "投赍": "投资",
    "筹赍": "筹资", "现釐": "现金", "流量": "流量", "合幷": "合并",
    "母公旬": "母公司", "巳": "已", "己": "已", "末": "未",
    "曰": "日", "午": "年", "月仞": "月初", "度末": "期末",
    "度初": "期初", "年末": "年末",
    "OO": "00", "o0": "00", "l,": "1,", "S,": "5,", "T,": "7,",
}
# 降序排键 -> 长匹配优先
_SORTED_KEYS = sorted(_CORRECTION_MAP.keys(), key=len, reverse=True)
_CORRECTION_RE = re.compile('|'.join(re.escape(k) for k in _SORTED_KEYS))

# ── 数字格式修正 ──
_NUMBER_FIXES_RE = re.compile(r'[–—]\s*(\d)')  # 负号变连字符: –500 -> -500

# ── 单位行识别 ──
_UNIT_LINE_PATTERN = re.compile(
    r'^[\s]*(?:单位[：:]|(?:人民币)?[元千百万元亿元]+[\s]*$|(?:in\s+)?(?:RMB|CNY|thousands?|millions?))',
    re.IGNORECASE,
)


def correct_ocr_errors(text: str) -> str:
    """OCR 文本纠错: 单次扫描 O(text_length)

    优化: 编译正则 dict callback 替代逐个 str.replace。
    40+ 个模式一次匹配完成，不再 O(nxm)。
    """
    if not text:
        return text
    original = text
    text = _CORRECTION_RE.sub(lambda m: _CORRECTION_MAP[m.group()], text)
    text = _NUMBER_FIXES_RE.sub(r'-\1', text)
    if text != original:
        logger.debug(f"OCR 纠错: {len(original)-len(text)} 字符变化")
    return text


def correct_row_text(row_dict: dict) -> dict:
    """对一行 OCR 数据 (如 RawTableRow) 的所有文本列进行纠错

    输入: {"row_index": 0, "columns": {"col_0": "货币资釐", "col_1": "12,345.67"}, ...}
    输出: 同结构，文本值已纠错
    """
    if not row_dict or "columns" not in row_dict:
        return row_dict

    corrected_columns = {}
    for key, val in row_dict["columns"].items():
        if isinstance(val, str):
            corrected_columns[key] = correct_ocr_errors(val)
        else:
            corrected_columns[key] = val

    return {**row_dict, "columns": corrected_columns}


def correct_raw_document(raw_data: dict) -> dict:
    """对整个 RawDocument 的原始提取结果进行 OCR 纠错

    在 pdf_parser.extract() 输出后、chunker.chunk() 之前调用。
    """
    if not raw_data or "pages" not in raw_data:
        return raw_data

    corrected_pages = []
    for page in raw_data["pages"]:
        # 纠错文字
        text = page.get("text", "")
        corrected_text = correct_ocr_errors(text)

        # 纠错表格
        tables = page.get("tables", [])
        corrected_tables = []
        for table in tables:
            corrected_rows = []
            for row in table.get("rows", []):
                corrected_row = []
                for cell in row:
                    if isinstance(cell, str):
                        corrected_row.append(correct_ocr_errors(cell))
                    else:
                        corrected_row.append(cell)
                corrected_rows.append(corrected_row)
            corrected_tables.append({**table, "rows": corrected_rows})

        corrected_pages.append({
            **page,
            "text": corrected_text,
            "tables": corrected_tables,
        })

    total_fixed = sum(
        1 for p_orig, p_corr in zip(raw_data["pages"], corrected_pages)
        if p_orig.get("text") != p_corr.get("text")
    )
    if total_fixed:
        logger.info(f"OCR 纠错: {total_fixed}/{len(corrected_pages)} 页有修改")

    return {**raw_data, "pages": corrected_pages}
