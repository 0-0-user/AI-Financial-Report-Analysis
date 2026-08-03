"""B0层: 表头语义指引——LLM 识别表头、支持横向/纵向布局 + 多级表头

v2 新增: 
- 横/纵向布局自动检测 (row_major vs column_major) 
- 多级合并表头解析 (3-4 行表头) 
- 英文变体匹配 (港股/美股) 
- 银行/保险/券商特殊科目识别
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

import yaml

from schemas.raw_doc import RawDocument, RawTableRow
from schemas.b0_guide import B0Guide, TableGuide, FieldMapping, ColumnHeader

logger = logging.getLogger(__name__)

# ── 中文列名 -> 标准名 ──
COLUMN_ALIASES: dict[str, str] = {
    "期末余额": "end_balance", "期末数": "end_balance",
    "年末余额": "end_balance", "期末": "end_balance",
    "期初余额": "begin_balance", "期初数": "begin_balance",
    "年初余额": "begin_balance", "期初": "begin_balance",
    "本期发生额": "current_amount", "上期发生额": "prior_amount",
    "上年同期": "prior_amount", "本期金额": "current_amount",
    "上期金额": "prior_amount", "金额": "amount",
    "占比": "percentage", "比例": "percentage",
}
# 英文
COLUMN_ALIASES.update({
    "End Balance": "end_balance", "Ending Balance": "end_balance",
    "Closing Balance": "end_balance", "As at": "end_balance",
    "Beginning Balance": "begin_balance", "Opening Balance": "begin_balance",
    "Current Period": "current_amount", "Prior Period": "prior_amount",
    "Amount": "amount",
})

# ── 单位检测 ──
_UNIT_PATTERNS = [
    (r"万元|万", "万元"),
    (r"千元|千", "千元"),
    (r"亿元|亿", "亿元"),
    (r"百万元|百万", "百万元"),
    (r"元[^/万股]|RMB|CNY", "元"),
    (r"millions?\s*(?:of\s*)?(?:RMB|CNY|yuan)", "百万元"),
    (r"thousands?\s*(?:of\s*)?(?:RMB|CNY|yuan)", "千元"),
]

# ── 报表类型检测 ──
_REPORT_TYPE_PATTERNS = [
    (r"合并|(?:consolidated|group)\s+(?:financial|balance)", "合并报表"),
    (r"母公司|(?:parent\s+company|单独)", "母公司报表"),
]

# ── 布局检测阈值 ──
_ROW_MAJOR_THRESHOLD = 3   # 列数>=3 -> 行式 (科目在行) | MinerU 财报表通常 3-4 列
_COL_MAJOR_THRESHOLD = 3   # 列数<=3 且 行数>=10 -> 列式 (科目在列) 

def run_semantic_guide(raw_doc: RawDocument) -> B0Guide:
    """LLM 只读表头 -> 输出字段映射指引

    合并报表（financials 主用）与母公司报表（供 B+ 母子资金分离度）分别引导，
    report_type 由 L0 归桶信息决定（母公司桶强制标"母公司报表"），不依赖表头重新检测。
    """
    fd = raw_doc.financial_data
    tables: list[TableGuide] = []
    sheet_map = [
        ("资产负债表", fd.balance_sheet, fd.parent_balance_sheet),
        ("利润表", fd.income_statement, fd.parent_income_statement),
        ("现金流量表", fd.cashflow_statement, fd.parent_cashflow_statement),
    ]
    for table_name, consolidated_rows, parent_rows in sheet_map:
        if consolidated_rows:
            guide = _process_one_table(table_name, consolidated_rows)
            if guide:
                guide.report_type = "合并报表"
                tables.append(guide)
        if parent_rows:
            guide = _process_one_table(table_name, parent_rows)
            if guide:
                guide.report_type = "母公司报表"
                tables.append(guide)
    return B0Guide(tables=tables, raw_document_name=raw_doc.metadata.file_name)


def _process_one_table(table_name: str, rows: list[RawTableRow]) -> Optional[TableGuide]:
    """处理一张报表: 布局检测 -> 表头解析 -> LLM/规则字段映射"""
    # 1. 检测布局方向
    is_row_major = _detect_layout(rows)

    # 2. 提取多级表头
    header_info = _parse_multi_level_header(rows, is_row_major)

    # 3. 字段映射 (LLM 优先, 失败规则降级)
    try:
        return _llm_guide(table_name, header_info, rows, is_row_major)
    except Exception as e:
        logger.warning(f"LLM 表头失败, 降级规则: {e}")
        return _rule_based_guide(table_name, header_info, rows, is_row_major)


# ═══════════════════════════════════════════════
# 布局检测
# ═══════════════════════════════════════════════

def _detect_layout(rows: list[RawTableRow]) -> bool:
    """检测表格是行式(row_major)还是列式(column_major)

    行式 (绝大多数中国年报) : 
      列: 项目 | 期末余额 | 期初余额
      行: 货币资金 | 100 | 90
           应收账款 | 50  | 45

    列式 (某些银行/港股) : 
      列: 项目 | 2024 | 2023
      行: 利息净收入 | 100 | 90
           手续费收入  | 30  | 25

    判断: 列数>=6 -> 行式，列数<=3 -> 列式
    """
    if not rows:
        return True
    # 取前 5 行看最多列数
    max_cols = max(len(row.columns) for row in rows[:5])
    return max_cols >= _ROW_MAJOR_THRESHOLD


# ═══════════════════════════════════════════════
# 多级表头解析
# ═══════════════════════════════════════════════

def _parse_multi_level_header(
    rows: list[RawTableRow], is_row_major: bool
) -> dict:
    """解析多级合并表头，返回结构化信息

    输入 (典型 3 行表头) : 
      行0: |  资产        |   期末余额     |   期初余额     |
      行1: |  项目        | 合并   母公司  | 合并   母公司  |

    输出:
      {
        "header_rows": [行0, 行1],
        "layout": "row_major",
        "unit": "万元",
        "report_type": "合并报表",
        "data_start_row": 2,
        "field_col_index": 0,   # 科目名在哪一列
        "value_col_indices": [1,2],  # 数值在哪几列
        "header_text": "行0: 资产 | 期末余额 | 期初余额\n行1: 项目 | 合并 母公司 | 合并 母公司",
      }
    """
    max_rows = min(len(rows), 10)
    # 找数据起始行: 行式表格的数据通常在第 2~5 行开始
    data_start = _find_data_start(rows[:max_rows], is_row_major)

    # 表头行
    header_rows = rows[:data_start]
    header_text = "\n".join(
        f"行{r.row_index}: " + " | ".join(str(v) for v in r.columns.values())
        for r in header_rows
    )

    # 从表头文字检测单位和报表类型
    unit = _detect_unit(header_text)
    report_type = _detect_report_type(header_text)

    # 确定科目列和数据列
    field_col_idx = 0 if is_row_major else None  # 行式: 科目在第0列
    value_col_indices = _find_value_columns(header_rows) if is_row_major else []

    return {
        "header_rows": header_rows,
        "layout": "row_major" if is_row_major else "column_major",
        "unit": unit,
        "report_type": report_type,
        "data_start_row": data_start,
        "field_col_index": field_col_idx,
        "value_col_indices": value_col_indices,
        "header_text": header_text,
    }


def _find_data_start(rows: list[RawTableRow], is_row_major: bool) -> int:
    """找数据起始行: 跳过表头"""
    header_keywords = {"项目", "科目", "指标", "报表项目", "行次", "Items", "Item", "#"}
    for i, row in enumerate(rows):
        cols = row.columns
        row_text = " ".join(str(v) for v in cols.values())
        # 取第 0 列 (科目列) 的值判断是否含数字——日期列不算
        field_col = str(cols.get("col_0", ""))
        field_has_digit = bool(re.search(r'\d', field_col))
        # 整行其他列可能含年份 (如"2025年12月31日") ，这些不算是数据行特征
        # 表头特征: 科目列含关键词 且 科目列无数字
        is_header = any(kw in row_text for kw in header_keywords) and not field_has_digit
        if not is_header:
            return i
    return min(3, len(rows))  # 默认前 3 行为表头


def _find_value_columns(header_rows: list[RawTableRow]) -> list[int]:
    """从表头行中识别数值列"""
    value_cols = set()
    value_keywords = {"余额", "金额", "发生额", "同期", "期末", "期初", "本期",
                       "Balance", "Amount", "Period", "End", "Begin"}
    for row in header_rows:
        for col_key, col_val in row.columns.items():
            text = str(col_val)
            if any(kw in text for kw in value_keywords):
                col_num = int(col_key.replace("col_", ""))
                value_cols.add(col_num)
    return sorted(value_cols) if value_cols else [1, 2]


def _detect_unit(text: str) -> str:
    for pattern, unit in _UNIT_PATTERNS:
        if re.search(pattern, text):
            return unit
    return "元"


def _detect_report_type(text: str) -> str:
    for pattern, rtype in _REPORT_TYPE_PATTERNS:
        if re.search(pattern, text):
            return rtype
    return "合并报表"


# ═══════════════════════════════════════════════
# LLM 路径
# ═══════════════════════════════════════════════

def _llm_guide(
    table_name: str, header_info: dict,
    all_rows: list[RawTableRow], is_row_major: bool,
) -> TableGuide:
    from llm.client import LLMClient
    client = LLMClient()

    # 提取全部唯一原始字段名 (从 col_0，表头之后) ，让 LLM 做标准名映射
    # 最多 50 个字段，覆盖三大报表全部关键行；超过会导致 LLM 输出被截断
    sample_field_names = []
    for row in all_rows[header_info["data_start_row"]:]:
        raw_name = str(row.columns.get("col_0", "")).strip()
        if raw_name and raw_name not in sample_field_names:
            sample_field_names.append(raw_name)
        if len(sample_field_names) >= 100:
            break

    response = client.chat(
        "b0_semantic_guide",
        {
            "table_headers": f"报表: {table_name}\n布局: {header_info['layout']}\n{header_info['header_text']}\n\n"
                             f"原始字段名列表 (前{len(sample_field_names)}个) :\n" +
                             "\n".join(f"  {i+1}. {name}" for i, name in enumerate(sample_field_names))
        },
        temperature=0.0,
        max_tokens=16384,
    )
    data = _parse_llm_response(str(response) if not isinstance(response, str) else response)

    columns = []
    raw_cols = data.get("columns", {})
    if isinstance(raw_cols, dict):
        for k, v in raw_cols.items():
            columns.append(ColumnHeader(index=0, raw_text=str(k), standard_name=str(v)))

    field_mappings = []
    for fm in data.get("field_mapping", []):
        loc = fm.get("location", {})
        raw_name = fm.get("raw_name", "")
        field_mappings.append(FieldMapping(
            raw_name=raw_name,
            standard_name=fm.get("standard_name", raw_name),
            row_index=loc.get("row_start", 0),
            col_index=loc.get("col_index", 0),
            unit=fm.get("unit") or header_info["unit"],
            is_negative=fm.get("is_negative", False),
        ))

    return TableGuide(
        table_name=table_name,
        report_type=header_info["report_type"],
        overall_unit=header_info["unit"],
        columns=columns,
        field_mappings=field_mappings,
        layout=header_info["layout"],
    )


def _parse_llm_response(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r'\{[\s\S]*\}', raw)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"LLM 返回的 JSON 不完整（可能被截断）: {e}"
                ) from e
        raise ValueError("LLM 返回的内容不是有效 JSON")


# ═══════════════════════════════════════════════
# 规则降级 (无 LLM 时)
# ═══════════════════════════════════════════════

_FIELDS_CFG = Path("config/financial_fields.yaml")
_VARIANT_IDX: dict[str, str] | None = None


def _build_variant_index() -> dict[str, str]:
    global _VARIANT_IDX
    if _VARIANT_IDX is not None:
        return _VARIANT_IDX
    idx: dict[str, str] = {}
    if _FIELDS_CFG.exists():
        with open(_FIELDS_CFG, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        fields = cfg.get("fields", {})
        eng = cfg.get("field_english_aliases", {})
        for sn, info in fields.items():
            for v in info.get("chinese_variants", []):
                idx[v] = sn
            for v in eng.get(sn, []):
                idx[v.lower()] = sn
    _VARIANT_IDX = idx
    return idx


def _rule_based_guide(
    table_name: str, header_info: dict,
    all_rows: list[RawTableRow], is_row_major: bool,
) -> TableGuide:
    """规则匹配: col_0 全字优先 + 子串兜底"""
    idx = _build_variant_index()
    data_rows = all_rows[header_info["data_start_row"]:]
    value_cols = header_info.get("value_col_indices", [1, 2])
    field_mappings: list[FieldMapping] = []

    for row in data_rows:
        cols = row.columns
        if not cols:
            continue
        col0 = str(list(cols.values())[0]).replace("\n", "").strip()
        row_text = " ".join(str(v).replace("\n", " ") for v in cols.values())

        matched = False
        if col0 in idx:
            field_mappings.append(FieldMapping(raw_name=col0, standard_name=idx[col0],
                row_index=row.row_index, col_index=value_cols[0] if value_cols else 1,
                unit=header_info["unit"]))
            matched = True
        if not matched:
            for variant, std_name in idx.items():
                if variant in col0 or col0 in variant:
                    field_mappings.append(FieldMapping(raw_name=variant, standard_name=std_name,
                        row_index=row.row_index, col_index=value_cols[0] if value_cols else 1,
                        unit=header_info["unit"]))
                    break

    columns = [ColumnHeader(index=vc, raw_text=f"val{i}", standard_name=f"value_{i}")
               for i, vc in enumerate(value_cols)]
    return TableGuide(table_name=table_name, report_type=header_info["report_type"],
        overall_unit=header_info["unit"], columns=columns, field_mappings=field_mappings,
        layout=header_info["layout"])
