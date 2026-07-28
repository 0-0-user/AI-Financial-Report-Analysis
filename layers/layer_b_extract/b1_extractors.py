"""B1层：代码准确提取表格数据——纯 Pandas 定位取数

职责：
- 根据 B0 层的 FieldMapping 指引，从 RawTableRow 中精确取数
- 统一换算为"元"
- 组装为 FinancialStatement（三大报表 + 校验占位）

设计约束（架构要求）：
- 纯 Pandas/numpy 代码，不涉及任何 LLM 调用
- 不做语义判断，只做机械操作
- 字段映射来自 B0 层指引，不自己猜
"""

import logging
import re
from typing import Optional

import numpy as np

from schemas.raw_doc import RawDocument, RawTableRow
from schemas.b0_guide import B0Guide, FieldMapping
from schemas.financial import FinancialStatement, FinancialField, ValidationResult

logger = logging.getLogger(__name__)


def run_extraction(raw_doc: RawDocument, guide: B0Guide) -> FinancialStatement:
    """纯代码：根据 B0 指引定位表格行列，提取数字并换算单位

    Args:
        raw_doc: 第0层的 RawDocument（含原始 OCR 表格行）
        guide: B0 层的 B0Guide（含字段映射指引）

    Returns:
        FinancialStatement（三大报表数据，所有值统一为"元"）
    """
    # 构建 {表名: RawTableRow列表} 映射
    all_rows: dict[str, list[RawTableRow]] = {
        "资产负债表": list(raw_doc.financial_data.balance_sheet),
        "利润表": list(raw_doc.financial_data.income_statement),
        "现金流量表": list(raw_doc.financial_data.cashflow_statement),
    }

    balance_sheet: dict[str, FinancialField] = {}
    income_statement: dict[str, FinancialField] = {}
    cashflow: dict[str, FinancialField] = {}

    for table_guide in guide.tables:
        table_name = table_guide.table_name
        rows = all_rows.get(table_name, [])
        overall_unit = table_guide.overall_unit
        multiplier = _unit_to_multiplier(overall_unit)

        for fm in table_guide.field_mappings:
            value = _extract_value(rows, fm)
            if value is None:
                continue

            # 单位换算：统一为"元"
            field_multiplier = _unit_to_multiplier(fm.unit)
            value_in_yuan = value * field_multiplier * (multiplier if fm.unit == overall_unit else 1)

            if fm.is_negative:
                value_in_yuan = -abs(value_in_yuan)

            field = FinancialField(
                standard_name=fm.standard_name,
                raw_name=fm.raw_name,
                value=round(value_in_yuan, 2),
                original_unit=fm.unit or overall_unit,
                report_type=table_guide.report_type,
            )

            # 分配到对应报表
            if table_name in ("资产负债表",):
                balance_sheet[fm.standard_name] = field
            elif table_name in ("利润表",):
                income_statement[fm.standard_name] = field
            elif table_name in ("现金流量表",):
                cashflow[fm.standard_name] = field

    # 提取元数据
    company_name = raw_doc.company_overview.company_name or ""
    stock_code = raw_doc.company_overview.stock_code or ""
    report_year = raw_doc.metadata.report_year or 0
    report_type = guide.tables[0].report_type if guide.tables else "合并报表"

    return FinancialStatement(
        company_name=company_name,
        stock_code=stock_code,
        year=report_year,
        report_type=report_type,
        balance_sheet=balance_sheet,
        income_statement=income_statement,
        cashflow=cashflow,
        validation=ValidationResult(is_valid=True, checks=[]),  # B1 校验在 b1_validators 中
    )


# ────────────────────────────────────────
# 数值提取
# ────────────────────────────────────────

def _extract_value(rows: list[RawTableRow], fm: FieldMapping) -> Optional[float]:
    """从表格行中按坐标提取数值

    策略（按优先级）：
    1. 如果 FieldMapping 有精确 row_index，直接定位
    2. 否则在行中搜索 raw_name 匹配
    3. 取对应列的数值
    """
    row_index = fm.row_index
    col_index = fm.col_index

    # 策略1：精确坐标
    if 0 <= row_index < len(rows):
        row = rows[row_index]
        col_key = f"col_{col_index}"
        val_str = row.columns.get(col_key, "")
        if val_str:
            parsed = _parse_number(val_str)
            if parsed is not None:
                return parsed

    # 策略2：遍历搜索 raw_name 匹配的行
    for row in rows:
        row_text = " ".join(str(v) for v in row.columns.values())
        if fm.raw_name and fm.raw_name in row_text:
            # 找到字段名所在行 → 在同行或下一行找数值
            val_str = _find_numeric_column(row.columns)
            if val_str:
                parsed = _parse_number(val_str)
                if parsed is not None:
                    return parsed
            # 也检查下一行（有些表格字段名和数值分行）
            next_idx = rows.index(row) + 1
            if next_idx < len(rows):
                next_row = rows[next_idx]
                val_str = _find_numeric_column(next_row.columns)
                if val_str:
                    parsed = _parse_number(val_str)
                    if parsed is not None:
                        return parsed

    return None


def _find_numeric_column(columns: dict[str, str]) -> Optional[str]:
    """在行内找到第一个包含数字的列值"""
    for val in columns.values():
        cleaned = str(val).replace(",", "").replace(" ", "").replace("%", "")
        if cleaned and any(c.isdigit() for c in cleaned):
            return str(val)
    return None


def _parse_number(text: str) -> Optional[float]:
    """从文本中解析数字

    支持格式：
    - "12,345,678.90"
    - "1,234,567"
    - "- 500"（某些 OCR 把负号识别为前导连字符）
    - "（500）"（中文括号表负数）
    - "12.5%" → 0.125
    """
    if not text:
        return None

    text = str(text).strip()

    # 百分比
    is_pct = text.endswith("%")
    if is_pct:
        text = text[:-1]

    # 中文括号表负数： （500）→ -500
    negative = False
    if text.startswith("（") and text.endswith("）"):
        negative = True
        text = text[1:-1]
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    if text.startswith("-") or text.startswith("–") or text.startswith("—"):
        negative = True
        text = text[1:]

    # 去除分隔符
    text = text.replace(",", "").replace(" ", "").strip()

    # 尝试解析
    try:
        value = float(text)
    except ValueError:
        # 可能有前导非数字字符（如"约"、"大约"）
        cleaned = re.sub(r'[^\d.\-]', '', text)
        if not cleaned:
            return None
        try:
            value = float(cleaned)
        except ValueError:
            return None

    if negative:
        value = -value
    if is_pct:
        value = value / 100.0

    # 过滤极端异常值（年报不应出现超过10^15的数值）
    if abs(value) > 1e15:
        logger.warning(f"数值异常: {text} → {value}")
        return None

    return value


# ────────────────────────────────────────
# 单位换算
# ────────────────────────────────────────

def _unit_to_multiplier(unit: str) -> float:
    """单位字符串 → 数字乘数（统一换算为"元"）"""
    mapping = {
        "元": 1,
        "千元": 1_000,
        "万元": 10_000,
        "亿元": 100_000_000,
        "百万元": 1_000_000,
        "元/股": 1,  # 每股数据不换算
    }
    return mapping.get(unit, 1)
