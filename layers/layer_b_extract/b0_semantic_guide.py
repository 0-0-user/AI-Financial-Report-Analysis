"""B0层：表头语义指引——LLM 识别表头、支持横向/纵向布局 + 多级表头

v2 新增：
- 横/纵向布局自动检测（row_major vs column_major）
- 多级合并表头解析（3-4 行表头）
- 英文变体匹配（港股/美股）
- 银行/保险/券商特殊科目识别
"""

import json
import logging
import re
import yaml
from pathlib import Path
from typing import Optional

from schemas.raw_doc import RawDocument, RawTableRow
from schemas.b0_guide import B0Guide, TableGuide, FieldMapping, ColumnHeader

logger = logging.getLogger(__name__)

FIELDS_CONFIG_PATH = Path("config/financial_fields.yaml")

# ── 中文列名 → 标准名 ──
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
_ROW_MAJOR_THRESHOLD = 6   # 列数≥6 → 行式（科目在行）
_COL_MAJOR_THRESHOLD = 3   # 列数≤3 且 行数≥10 → 列式（科目在列）


def run_semantic_guide(raw_doc: RawDocument) -> B0Guide:
    """LLM 只读表头 → 输出字段映射指引"""
    tables: list[TableGuide] = []
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
    return B0Guide(tables=tables, raw_document_name=raw_doc.metadata.file_name)


def _process_one_table(table_name: str, rows: list[RawTableRow]) -> Optional[TableGuide]:
    """处理一张报表：布局检测 → 表头解析 → LLM/规则字段映射"""
    # 1. 检测布局方向
    is_row_major = _detect_layout(rows)

    # 2. 提取多级表头
    header_info = _parse_multi_level_header(rows, is_row_major)

    # 3. 字段映射
    try:
        return _llm_guide(table_name, header_info, rows, is_row_major)
    except Exception as e:
        logger.warning(f"LLM 表头识别失败，降级规则匹配: {e}")
        return _rule_based_guide(table_name, header_info, rows, is_row_major)


# ═══════════════════════════════════════════════
# 布局检测
# ═══════════════════════════════════════════════

def _detect_layout(rows: list[RawTableRow]) -> bool:
    """检测表格是行式(row_major)还是列式(column_major)

    行式（绝大多数中国年报）：
      列: 项目 | 期末余额 | 期初余额
      行: 货币资金 | 100 | 90
           应收账款 | 50  | 45

    列式（某些银行/港股）：
      列: 项目 | 2024 | 2023
      行: 利息净收入 | 100 | 90
           手续费收入  | 30  | 25

    判断：列数≥6 → 行式，列数≤3 → 列式
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

    输入（典型 3 行表头）：
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
    # 找数据起始行：行式表格的数据通常在第 2~5 行开始
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
    field_col_idx = 0 if is_row_major else None  # 行式：科目在第0列
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
    """找数据起始行：跳过表头"""
    header_keywords = {"项目", "科目", "指标", "报表项目", "行次", "Items", "Item", "#"}
    for i, row in enumerate(rows):
        cols = row.columns
        row_text = " ".join(str(v) for v in cols.values())
        # 表头特征：含关键词 且 不含数字
        has_digit = any(re.search(r'\d', str(v)) for v in cols.values())
        is_header = any(kw in row_text for kw in header_keywords) and not has_digit
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
    response = client.chat(
        "b0_semantic_guide",
        {"table_headers": f"报表: {table_name}\n布局: {header_info['layout']}\n{header_info['header_text']}"},
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
        field_mappings.append(FieldMapping(
            raw_name=fm.get("raw_name", ""),
            standard_name=fm.get("standard_name", ""),
            row_index=loc.get("row_start", 0),
            col_index=loc.get("col_index", 0),
            unit=fm.get("unit", header_info["unit"]),
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
            except json.JSONDecodeError:
                pass
        return {}


# ═══════════════════════════════════════════════
# 规则降级（含英文 + 跨行业字段）
# ═══════════════════════════════════════════════

def _rule_based_guide(
    table_name: str, header_info: dict,
    all_rows: list[RawTableRow], is_row_major: bool,
) -> TableGuide:
    """规则匹配：中文 + 英文变体全覆盖"""
    if not FIELDS_CONFIG_PATH.exists():
        return _empty_guide(table_name, header_info)

    with open(FIELDS_CONFIG_PATH, encoding="utf-8") as f:
        field_config = yaml.safe_load(f)
    fields = field_config.get("fields", {})
    english_aliases = field_config.get("field_english_aliases", {})
    small_aliases = field_config.get("small_enterprise_aliases", {})

    field_mappings = []
    data_rows = all_rows[header_info["data_start_row"]:]
    col_idx = header_info.get("field_col_index", 0)
    value_cols = header_info.get("value_col_indices", [1, 2])

    for row in data_rows:
        row_text = " ".join(str(v) for v in row.columns.values())
        row_text_lower = row_text.lower()

        # 匹配标准字段（中文变体 + 英文变体）
        best_match = _find_best_field_match(row_text_lower, fields, english_aliases)
        if best_match:
            field_mappings.append(FieldMapping(
                raw_name=best_match[0], standard_name=best_match[1],
                row_index=row.row_index,
                col_index=value_cols[0] if value_cols else 1,
                unit=header_info["unit"],
            ))
            continue  # 已匹配，不重复

        # 匹配小微企业简版别名
        for cn_name, std_name in small_aliases.items():
            if cn_name in row_text and std_name not in {fm.standard_name for fm in field_mappings}:
                field_mappings.append(FieldMapping(
                    raw_name=cn_name, standard_name=std_name,
                    row_index=row.row_index,
                    col_index=value_cols[0] if value_cols else 1,
                    unit=header_info["unit"],
                ))

    columns = []
    for i, col_idx_v in enumerate(value_cols):
        cn = f"数值列{i+1}"
        columns.append(ColumnHeader(index=col_idx_v, raw_text=cn, standard_name=f"value_{i}"))

    return TableGuide(
        table_name=table_name,
        report_type=header_info["report_type"],
        overall_unit=header_info["unit"],
        columns=columns,
        field_mappings=field_mappings,
        layout=header_info["layout"],
    )


# ═══════════════════════════════════════════════
# 模糊匹配（OCR 纠错后的二次保障）
# ═══════════════════════════════════════════════

def _find_best_field_match(
    row_text: str, fields: dict, english_aliases: dict,
) -> tuple[str, str] | None:
    """在行文本中找到最佳字段匹配

    策略（按优先级）：
    1. 精确子串匹配（中文变体 + 英文变体）
    2. 模糊匹配（Levenshtein距离 ≤ 2）—— OCR 纠错后的二次保障
    3. 都不匹配 → None

    Returns:
        (raw_name, standard_name) 或 None
    """
    # 第1轮：精确匹配
    for std_name, f_info in fields.items():
        variants = list(f_info.get("chinese_variants", []))
        variants.extend(english_aliases.get(std_name, []))
        for variant in variants:
            if variant.lower() in row_text:
                return (variant, std_name)

    # 第2轮：模糊匹配（Levenshtein ≤ 2）
    # 提取行文本中的候选片段（以空格/标点分割的 token）
    import re
    tokens = re.split(r'[\s\|,，;；、。]+', row_text)
    best_score = 999
    best_match = None

    for token in tokens:
        token = token.strip()
        if len(token) < 3:
            continue
        for std_name, f_info in fields.items():
            variants = list(f_info.get("chinese_variants", []))
            variants.extend(english_aliases.get(std_name, []))
            for variant in variants:
                score = _levenshtein_distance(token, variant.lower())
                if score <= 2 and score < best_score:
                    best_score = score
                    best_match = (variant, std_name)

    return best_match


def _levenshtein_distance(a: str, b: str) -> int:
    """计算两个字符串的编辑距离（Levenshtein Distance）

    纯 Python 实现，不依赖第三方库。
    O(n*m) 时间，O(min(n,m)) 空间。
    """
    if len(a) < len(b):
        a, b = b, a
    if len(b) == 0:
        return len(a)

    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(
                prev[j] + 1,          # 删除
                curr[j - 1] + 1,      # 插入
                prev[j - 1] + (0 if ca == cb else 1),  # 替换
            ))
        prev = curr
    return prev[-1]


def _empty_guide(table_name: str, header_info: dict) -> TableGuide:
    return TableGuide(
        table_name=table_name, report_type=header_info["report_type"],
        overall_unit=header_info["unit"], columns=[], field_mappings=[],
        layout=header_info["layout"],
    )
