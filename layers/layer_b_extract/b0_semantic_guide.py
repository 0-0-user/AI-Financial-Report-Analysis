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

def _column_texts(header_info: dict) -> list[list[str]]:
    """每一列表头行里出现过的文字 (按列号, 去重保序)。

    提示词 grounding (_describe_columns) 和语义识别 (_column_headers)
    都要这份东西 —— 两边各写一遍的话, 改了一处漏另一处,
    提示词说的和守卫认的就对不上了。
    """
    header_rows = header_info.get("header_rows") or []
    n_cols = max((len(r.columns) for r in header_rows), default=0)
    out: list[list[str]] = []
    for j in range(n_cols):
        parts: list[str] = []
        for r in header_rows:
            text = str(r.columns.get(f"col_{j}", "")).strip()
            if text and text not in parts:
                parts.append(text)
        out.append(parts)
    return out


def _describe_columns(header_info: dict) -> str:
    """把 col_j 和它上方表头行的文字绑成对照表，供提示词使用。

    LLM 拿到一张表时无从知道 col_0 是科目名列、col_1 才是数值列。
    不写这段，col_index 就只能靠猜 —— 实测同一提示词重复跑会在 0 和 1
    之间跳，而指到 col_0（科目名列）会让 `_parse_number` 返回 None，
    字段被 B1 静默丢弃。
    """
    return "\n".join(
        f"  col_{j} = " + " / ".join(parts)
        for j, parts in enumerate(_column_texts(header_info)) if parts
    )


def _period_standard_name(header_texts: list[str]) -> str:
    """列头文字 -> 期间标准名 (end_balance / begin_balance / ...); 认不出返回空串。

    按别名长度**倒序**匹配 —— "期末余额" 必须比 "期末" 先命中, 否则更具体的
    别名永远轮不到。
    """
    for alias in sorted(COLUMN_ALIASES, key=len, reverse=True):
        if any(alias in t for t in header_texts):
            return COLUMN_ALIASES[alias]
    return ""


# 列头里的日期: "2025年12月31日" / "2025年度" / "2025年12月"
_HEADER_DATE_RE = re.compile(
    r"((?:19|20)\d{2})\s*年(?:\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?)?")


def _header_date(header_texts: list[str]) -> Optional[tuple[int, int, int]]:
    """列头文字里的日期, 归一成可比较的 (年, 月, 日); 认不出返回 None。

    "2025年12月31日" -> (2025, 12, 31)
    "2025年度"       -> (2025, 0, 0)   —— 期间型表头, 没有月日
    """
    for t in header_texts:
        m = _HEADER_DATE_RE.search(t)
        if m:
            return (int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0))
    return None


def _date_period_names(table_name: str) -> tuple[str, str]:
    """日期列头下的 (本期, 上期) 标准名。

    资产负债表是**时点**, 利润表/现金流量表是**期间** —— 名字分开,
    下游读语义时不会失真。两者的上期名都算上期列 (见 b1 的
    _PRIOR_PERIOD_STANDARD_NAMES), 所以守卫对两张表都管用。
    """
    if "资产负债表" in table_name:
        return "end_balance", "begin_balance"
    return "current_amount", "prior_amount"


def _column_headers(
    header_info: dict, table_name: str = "",
) -> list[ColumnHeader]:
    """给每一列建 ColumnHeader, 带上**真实列号**和期间语义。

    旧写法把 index 一律填 0、standard_name 填成占位的 value_i, 下游于是
    无从校验 LLM 给的 col_index —— 指到「期初余额」列时那一列**确实**
    解析得出数, 会被照单全收, 静默把期初当期末。

    期间语义直接读表头文字, **不经过 LLM**: 这是表头上明摆着的字,
    没必要让模型再猜一遍。

    两条识别路径, 缺一不可:

      1. 别名 —— "期末余额/期初余额/本期金额".
      2. **日期排名** —— "2025年12月31日 / 2024年12月31日".
         真实年报基本只有这一条: 苏美达五张核心报表的列头全是日期,
         一个"期末"字样都没有。只做 (1) 的话, standard_name 全空,
         _prior_period_columns 恒为空集, 整个期间守卫**静默失效** ——
         不报错, 只是永远放行。
    """
    per_col = _column_texts(header_info)

    names = [_period_standard_name(t) for t in per_col]

    # 别名认不出两列以上时, 改用列头日期排名: 最新的 = 本期。
    if sum(1 for n in names if n) < 2:
        dated = [(j, d) for j, t in enumerate(per_col)
                 if (d := _header_date(t)) is not None]
        if len(dated) >= 2:
            latest = max(d for _j, d in dated)
            current, prior = _date_period_names(table_name)
            for j, d in dated:
                if not names[j]:
                    names[j] = current if d == latest else prior

    return [
        ColumnHeader(
            index=j,
            raw_text=" / ".join(per_col[j]),
            standard_name=names[j],
        )
        for j in range(len(per_col))
    ]


def _llm_guide(
    table_name: str, header_info: dict,
    all_rows: list[RawTableRow], is_row_major: bool,
) -> TableGuide:
    from llm.client import LLMClient
    client = LLMClient()

    # 提取全部唯一原始字段名 (从 col_0，表头之后) ，让 LLM 做标准名映射
    # 最多 100 个字段，覆盖三大报表全部关键行；超过会导致 LLM 输出被截断。
    # 带上 row.row_index（真下标）而不是 1-based 序号 —— LLM 要回填的
    # row_start 就是它，给它别的等于让它猜（见 _describe_columns 的说明）。
    sample_fields: list[tuple[int, str]] = []
    seen: set[str] = set()
    for row in all_rows[header_info["data_start_row"]:]:
        raw_name = str(row.columns.get("col_0", "")).strip()
        if raw_name and raw_name not in seen:
            seen.add(raw_name)
            sample_fields.append((row.row_index, raw_name))
        if len(sample_fields) >= 100:
            break

    col_legend = _describe_columns(header_info)
    field_lines = "\n".join(f"  行{idx}: {name}" for idx, name in sample_fields)

    response = client.chat(
        "b0_semantic_guide",
        {
            "table_headers": (
                f"报表: {table_name}\n布局: {header_info['layout']}\n"
                f"{header_info['header_text']}\n\n"
                f"列编号对照 (0-based) :\n{col_legend}\n\n"
                f"字段清单 (行号 | 字段名) ，共{len(sample_fields)}个。\n"
                f"row_start 请原样回填下面每行的**行号**，"
                f"它不是字段在清单里的第几个：\n{field_lines}"
            )
        },
        temperature=0.0,
        max_tokens=16384,
    )
    data = _parse_llm_response(str(response) if not isinstance(response, str) else response)

    # 列语义直接从表头文字读 (见 _column_headers) —— 不再取 LLM 的 columns:
    # 它按列名而不是列号给, 落不到 col_j 上, 旧写法只能一律填 index=0,
    # 等于把列号信息扔了。
    columns = _column_headers(header_info, table_name)

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
            # 只认**单向**: 科目名里含别名 (variant in col0)。
            # 反向的 `col0 in variant` 会让短科目名被长别名吸走 ——
            # 科目名「营业收入」落进别名「上期营业收入」, 于是本期营收被
            # 配到上期字段上, 算出来的同比恒为 0。
            # 这一层仍是**声明顺序优先**(先命中的 variant 胜): 别名互相
            # 包含时, 只有"更具体的那个先声明"才配对。要根治得改成
            # "最长别名优先", 本轮没做 (见提交说明)。
            for variant, std_name in idx.items():
                if variant in col0:
                    field_mappings.append(FieldMapping(raw_name=variant, standard_name=std_name,
                        row_index=row.row_index, col_index=value_cols[0] if value_cols else 1,
                        unit=header_info["unit"]))
                    break

    columns = _column_headers(header_info, table_name)
    return TableGuide(table_name=table_name, report_type=header_info["report_type"],
        overall_unit=header_info["unit"], columns=columns, field_mappings=field_mappings,
        layout=header_info["layout"])
