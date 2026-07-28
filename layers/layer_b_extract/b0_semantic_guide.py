"""B0层：表头语义指引——LLM 识别表头文字、字段映射和单位

职责：
- 读取 RawDocument.financial_data 的表头（只读文字，不碰数值）
- 调用 LLM 输出字段映射指引（B0Guide）
- 指引包含：报表类型、单位、每个字段的坐标 → 供 B1 层 Pandas 取数

LLM 不可用时降级：基于 config/financial_fields.yaml 的规则匹配
"""

import json
import logging
import yaml
from pathlib import Path

from schemas.raw_doc import RawDocument, RawTableRow
from schemas.b0_guide import B0Guide, TableGuide, FieldMapping, ColumnHeader

logger = logging.getLogger(__name__)

FIELDS_CONFIG_PATH = Path("config/financial_fields.yaml")

# 列名中文 → 标准名映射
COLUMN_ALIASES = {
    "期末余额": "end_balance",
    "期末数": "end_balance",
    "年末余额": "end_balance",
    "期末": "end_balance",
    "期初余额": "begin_balance",
    "期初数": "begin_balance",
    "年初余额": "begin_balance",
    "期初": "begin_balance",
    "本期发生额": "current_amount",
    "上期发生额": "prior_amount",
    "上年同期": "prior_amount",
    "本期金额": "current_amount",
    "上期金额": "prior_amount",
    "金额": "amount",
    "占比": "percentage",
    "比例": "percentage",
}


def run_semantic_guide(raw_doc: RawDocument) -> B0Guide:
    """大模型只读表头文字，输出字段映射指引

    Args:
        raw_doc: 第0层输出的 RawDocument

    Returns:
        B0Guide（含所有报表的 TableGuide 列表）
    """
    tables = []

    sheet_map = {
        "资产负债表": raw_doc.financial_data.balance_sheet,
        "利润表": raw_doc.financial_data.income_statement,
        "现金流量表": raw_doc.financial_data.cashflow_statement,
    }

    for table_name, rows in sheet_map.items():
        if not rows:
            continue

        guide = _process_one_table(table_name, rows)
        if guide:
            tables.append(guide)

    return B0Guide(
        tables=tables,
        raw_document_name=raw_doc.metadata.file_name,
    )


def _process_one_table(table_name: str, rows: list[RawTableRow]) -> TableGuide | None:
    """处理一张报表：识别表头 → LLM 字段映射 → 输出 TableGuide"""
    # 提取表头行（前几行）
    header_rows = _extract_header_rows(rows, max_rows=8)
    header_text = _format_header_for_llm(header_rows)

    # 尝试 LLM
    try:
        return _llm_guide(table_name, header_text, rows)
    except Exception as e:
        logger.warning(f"LLM 表头识别失败，降级为规则匹配: {e}")
        return _rule_based_guide(table_name, header_text, rows)


# ────────────────────────────────────────
# 表头提取
# ────────────────────────────────────────

def _extract_header_rows(rows: list[RawTableRow], max_rows: int) -> list[RawTableRow]:
    """提取表头行（前 max_rows 行）"""
    return rows[:max_rows]


def _format_header_for_llm(header_rows: list[RawTableRow]) -> str:
    """将表头行格式化为 LLM 可读的文字"""
    lines = []
    for row in header_rows:
        cols = row.columns
        if cols:
            line = " | ".join(f"{k}:{v}" for k, v in cols.items())
            lines.append(f"行{row.row_index}(第{row.page_number}页): {line}")
    return "\n".join(lines)


# ────────────────────────────────────────
# LLM 路径
# ────────────────────────────────────────

def _llm_guide(table_name: str, header_text: str, all_rows: list[RawTableRow]) -> TableGuide:
    """调用 LLM 识别表头，输出字段映射"""
    from llm.client import LLMClient

    client = LLMClient()
    response = client.chat(
        "b0_semantic_guide",
        {"table_headers": f"报表名称：{table_name}\n\n表头内容：\n{header_text}"},
    )

    raw = response if isinstance(response, str) else str(response)
    data = _parse_llm_response(raw)

    # 组装 TableGuide
    columns = []
    for col_data in data.get("columns", {}):
        for raw_text, std_name in col_data.items() if isinstance(col_data, dict) else []:
            columns.append(ColumnHeader(index=0, raw_text=raw_text, standard_name=std_name))

    field_mappings = []
    for fm in data.get("field_mapping", []):
        location = fm.get("location", {})
        field_mappings.append(FieldMapping(
            raw_name=fm.get("raw_name", ""),
            standard_name=fm.get("standard_name", ""),
            row_index=location.get("row_start", 0),
            col_index=location.get("col_index", 0),
            unit=fm.get("unit", data.get("unit", "元")),
            is_negative=fm.get("is_negative", False),
        ))

    return TableGuide(
        table_name=table_name,
        report_type=data.get("report_type", "合并报表"),
        overall_unit=data.get("unit", "元"),
        columns=columns,
        field_mappings=field_mappings,
    )


def _parse_llm_response(raw: str) -> dict:
    """解析 LLM 输出的 JSON"""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        import re
        match = re.search(r'\{[\s\S]*\}', raw)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        logger.warning(f"B0 LLM 输出解析失败: {raw[:200]}")
        return {}


# ────────────────────────────────────────
# 规则降级路径
# ────────────────────────────────────────

def _rule_based_guide(table_name: str, header_text: str, all_rows: list[RawTableRow]) -> TableGuide:
    """基于 config/financial_fields.yaml 的规则匹配（无 LLM 降级）

    策略：
    1. 加载字段映射表
    2. 遍历表头行，尝试匹配每个中文变体到标准字段名
    3. 根据行号确定每个字段的 row_index
    """
    # 加载字段配置
    if not FIELDS_CONFIG_PATH.exists():
        return TableGuide(
            table_name=table_name,
            report_type="合并报表",
            overall_unit=_detect_unit(header_text),
            columns=_detect_columns(header_text),
            field_mappings=[],
        )

    with open(FIELDS_CONFIG_PATH, encoding="utf-8") as f:
        field_config = yaml.safe_load(f)
    fields = field_config.get("fields", {})

    # 尝试匹配字段
    field_mappings = []
    for row in all_rows:
        cols = row.columns
        row_text = " ".join(str(v) for v in cols.values())

        for std_name, field_info in fields.items():
            variants = field_info.get("chinese_variants", [])
            for variant in variants:
                if variant in row_text:
                    # 找到匹配：确定该字段在哪个列
                    col_idx = _find_value_column(row, variant)
                    field_mappings.append(FieldMapping(
                        raw_name=variant,
                        standard_name=std_name,
                        row_index=row.row_index,
                        col_index=col_idx,
                        unit=_detect_unit(header_text),
                    ))
                    break  # 一个行匹配一个字段就够了

    return TableGuide(
        table_name=table_name,
        report_type="合并报表",
        overall_unit=_detect_unit(header_text),
        columns=_detect_columns(header_text),
        field_mappings=field_mappings,
    )


def _detect_unit(text: str) -> str:
    """从表头文字中检测单位"""
    if "万元" in text:
        return "万元"
    if "千元" in text:
        return "千元"
    if "亿元" in text:
        return "亿元"
    return "元"


def _detect_columns(header_text: str) -> list[ColumnHeader]:
    """从表头识别列结构"""
    columns = []
    col_idx = 0
    for cn_text, std_name in COLUMN_ALIASES.items():
        if cn_text in header_text:
            columns.append(ColumnHeader(index=col_idx, raw_text=cn_text, standard_name=std_name))
            col_idx += 1
    return columns


def _find_value_column(row: RawTableRow, field_name: str) -> int:
    """找到字段名所在行中数值所在的列号"""
    cols = row.columns
    # 通常第0列是字段名，第1列或第2列是数值
    for col_key, col_val in cols.items():
        if field_name in str(col_val):
            # 字段名在这一列，数值通常在后面
            col_num = int(col_key.replace("col_", ""))
            # 尝试找到下一个有数字的列
            for offset in range(1, 4):
                next_key = f"col_{col_num + offset}"
                next_val = cols.get(next_key, "")
                if next_val and any(c.isdigit() for c in str(next_val)):
                    return col_num + offset
            return col_num + 1  # 默认下一列
    return 1  # 默认第1列为数值列
