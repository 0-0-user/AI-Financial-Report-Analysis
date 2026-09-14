"""B1层: 代码提取表格数据——纯 Pandas，支持行式/列式两种布局

v2 新增: 
- 列式 (column_major) 表格支持: 科目名在行、年份值在列
- 多级表头跳过: data_start_row 以上全部略过
- 英文格式数字解析: 逗号分隔 (1,234,567) 和括号负数 ((500)) 
- Decimal 精确运算: 避免浮点误差累积 (财务数据要求精确到分) 
- 交叉验证: 用会计恒等式验证提取数据质量，发现异常时自动修正
"""

import logging
import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from schemas.raw_doc import RawDocument, RawTableRow
from schemas.b0_guide import B0Guide, FieldMapping
from schemas.financial import FinancialStatement, FinancialField, ValidationResult

logger = logging.getLogger(__name__)

# 金额统一保留 2 位小数 (分) 
_MONEY_QUANTIZE = Decimal("0.01")


def _pick_rows(
    raw_doc: RawDocument, table_name: str, is_parent: bool
) -> list[RawTableRow]:
    """按报表名 + 合并/母公司 选择对应桶的行"""
    fd = raw_doc.financial_data
    if table_name == "资产负债表":
        return list(fd.parent_balance_sheet) if is_parent else list(fd.balance_sheet)
    if table_name == "利润表":
        return list(fd.parent_income_statement) if is_parent else list(fd.income_statement)
    if table_name == "现金流量表":
        return list(fd.parent_cashflow_statement) if is_parent else list(fd.cashflow_statement)
    return []


def run_extraction(raw_doc: RawDocument, guide: B0Guide) -> tuple[FinancialStatement, FinancialStatement]:
    """根据 B0 指引定位取数，支持行式和列式表格

    合并报表（financials，供 B/C/D/E 分析）与母公司报表（parent_financials，
    供 B+ 母子资金分离度）分别提取，两者彻底分离。

    Returns:
        (financials, parent_financials) — 合并报表 + 母公司报表各一份
    """
    financials: dict[str, dict[str, FinancialField]] = {
        "balance_sheet": {}, "income_statement": {}, "cashflow": {},
    }
    parent: dict[str, dict[str, FinancialField]] = {
        "balance_sheet": {}, "income_statement": {}, "cashflow": {},
    }

    for table_guide in guide.tables:
        table_name = table_guide.table_name
        is_parent = table_guide.report_type == "母公司报表"
        rows = _pick_rows(raw_doc, table_name, is_parent)
        if not rows:
            continue

        target = parent if is_parent else financials
        layout = getattr(table_guide, "layout", "row_major")

        if layout == "column_major":
            _extract_column_major(
                rows, table_guide,
                target["balance_sheet"], target["income_statement"], target["cashflow"],
                table_name,
            )
        else:
            _extract_row_major(
                rows, table_guide,
                target["balance_sheet"], target["income_statement"], target["cashflow"],
                table_name,
            )

    # 交叉验证 + 自动修复（合并为主；母公司也做恒等式自检）
    financials["balance_sheet"] = _cross_validate_and_fix(financials["balance_sheet"])
    parent["balance_sheet"] = _cross_validate_and_fix(parent["balance_sheet"])

    # 代码级补充提取: 对 LLM 遗漏的字段，直接从原始行匹配 financial_fields.yaml（合并主用）
    financials["balance_sheet"], financials["income_statement"], financials["cashflow"] = _extract_all_known_fields(
        raw_doc, guide,
        financials["balance_sheet"], financials["income_statement"], financials["cashflow"],
    )
    # 关键合计字段精确提取: 资产总计/负债合计/所有者权益合计等用"科目列精确匹配+数值列"确定性提取，
    # 不依赖 LLM 映射质量（B0 漏映射/子串误匹配"流动负债合计"等都会导致勾稽失败）
    financials["balance_sheet"], financials["income_statement"], financials["cashflow"] = _extract_key_totals(
        raw_doc, guide,
        financials["balance_sheet"], financials["income_statement"], financials["cashflow"],
        use_parent=False,
    )
    parent["balance_sheet"], parent["income_statement"], parent["cashflow"] = _extract_key_totals(
        raw_doc, guide,
        parent["balance_sheet"], parent["income_statement"], parent["cashflow"],
        use_parent=True,
    )

    company_name = raw_doc.company_overview.company_name or ""
    stock_code = raw_doc.company_overview.stock_code or ""
    report_year = raw_doc.metadata.report_year or 0

    def _build(
        bs: dict, pl: dict, cf: dict, report_type: str,
    ) -> FinancialStatement:
        return FinancialStatement(
            company_name=company_name, stock_code=stock_code,
            year=report_year, report_type=report_type,
            balance_sheet=bs, income_statement=pl, cashflow=cf,
            validation=ValidationResult(is_valid=True, checks=[]),
        )

    main = _build(
        financials["balance_sheet"], financials["income_statement"], financials["cashflow"],
        "合并报表",
    )
    parent_fs = _build(
        parent["balance_sheet"], parent["income_statement"], parent["cashflow"],
        "母公司报表",
    )
    return main, parent_fs


# ═══════════════════════════════════════════════
# 行式提取 (原有逻辑，增强版) 
# ═══════════════════════════════════════════════

def _extract_row_major(
    rows: list[RawTableRow], guide,
    bs: dict, pl: dict, cf: dict, table_name: str,
):
    """行式表格: 预建行文本索引 O(rows + fields) 替代 O(rows x fields)"""
    multiplier = _unit_to_multiplier(guide.overall_unit)

    # v3: 预建索引 {raw_name -> (row, numeric_col_value)}
    prior_cols = _prior_period_columns(guide)
    row_index = _build_row_index(rows, guide.field_mappings, prior_cols)

    for fm in guide.field_mappings:
        entry = row_index.get(fm.raw_name)
        if entry is None:
            # fallback: 坐标取数
            value = _extract_value_fallback(rows, fm, guide)
        else:
            row, val_str = entry
            # 优先用 B0 映射的数值列（col_index），避免取到附注列（如"七、1"含数字被误当数值）。
            # 但要过两道关才采信 —— col_index 是 LLM 给的：
            #   1. 这一列得**真解析得出数**。实测会指到 col_0 科目名列，
            #      照单全收就会让 _parse_number("货币资金") 返回 None，
            #      把已经按名字定位好的字段反手丢掉。
            #   2. 这一列不能是上期/期初列。它解析得出数，但期间不对，
            #      放过去就是静默把期初当期末（见 _is_prior_period）。
            col_val_str = row.columns.get(f"col_{fm.col_index}", "")
            if (col_val_str.strip()
                    and _parse_number(col_val_str) is not None
                    and not _is_prior_period(prior_cols, fm.col_index)):
                val_str = col_val_str
            value = _parse_number(val_str) if val_str else None

        if value is None:
            continue
        field_mult = _unit_to_multiplier(fm.unit)
        d_value = Decimal(str(value))
        if fm.unit != guide.overall_unit:
            value_in_yuan = d_value * field_mult * multiplier
        else:
            value_in_yuan = d_value * field_mult
        value_in_yuan = value_in_yuan.quantize(_MONEY_QUANTIZE, rounding=ROUND_HALF_UP)
        if fm.is_negative:
            value_in_yuan = -abs(value_in_yuan)

        field = FinancialField(
            standard_name=fm.standard_name, raw_name=fm.raw_name,
            value=float(value_in_yuan),
            original_unit=fm.unit or guide.overall_unit,
            report_type=guide.report_type,
        )
        _assign_field(field, table_name, bs, pl, cf)


def _is_sub_item_row(row_text: str) -> bool:
    """判断是否合并报表内部的细分行（"归属于母公司…""少数股东…"）

    合计字段（如"所有者权益合计""净利润"）会被子串匹配到这些细分行而取错值，
    取数时必须跳过。
    """
    return ("归属于母公司" in row_text) or ("归属于母" in row_text) or ("少数股东" in row_text)


def _build_row_index(
    rows: list[RawTableRow], field_mappings: list[FieldMapping],
    skip_cols: frozenset[int] = frozenset(),
) -> dict[str, tuple[RawTableRow, str | None]]:
    """预建 raw_name -> (row, 数值列) 索引，O(rows)

    对每行提取科目名 (col_0 或 col_0+col_1) ，查 field_mappings 中
    的 raw_name 是否在行文本中 -> 匹配则记录该行和最新数值列。
    跳过"归属于母公司/少数股东"细分行，避免合计字段子串误匹配。
    `skip_cols` 是上期/期初列号 —— 它们不是可用的取数列。
    """
    idx: dict[str, tuple[RawTableRow, str | None]] = {}
    known_names = {fm.raw_name for fm in field_mappings}
    for row in rows:
        cols = row.columns
        row_text = " ".join(str(v) for v in cols.values())
        if _is_sub_item_row(row_text):
            continue
        for name in known_names:
            if name not in row_text:
                continue
            val_str = _find_numeric_column(cols, skip_cols)
            existing = idx.get(name)
            if existing is None:
                idx[name] = (row, val_str)
            elif val_str:
                # 保留绝对值更大的（合并报表 > 母公司 > 附注小表）
                try:
                    new_val = abs(_parse_number(val_str) or 0)
                    old_val = abs(_parse_number(existing[1]) or 0) if existing[1] else 0
                    if new_val > old_val:
                        idx[name] = (row, val_str)
                except (ValueError, TypeError):
                    pass
    return idx


# 「上期/期初」类列 —— 取数不能选它们。整套流水线要的是**本期期末**数。
_PRIOR_PERIOD_STANDARD_NAMES = frozenset({"begin_balance", "prior_amount"})


def _prior_period_columns(guide) -> frozenset[int]:
    """guide 里所有上期/期初列的列号。"""
    return frozenset(
        c.index for c in (getattr(guide, "columns", None) or [])
        if c.standard_name in _PRIOR_PERIOD_STANDARD_NAMES
    )


def _is_prior_period(prior_cols: frozenset[int], col_index: int) -> bool:
    """这一列是不是上期/期初列?

    是的话, 就算它**解析得出数**也不能要 —— 值本身合法但期间不对,
    放过去就是静默把期初当期末, 比丢字段更难发现 (报表里会明明白白
    印着一个数, 只是它是去年的)。

    拿不到列语义时 prior_cols 是空集, 于是恒为 False:
    不知道不等于错, 不能因为不知道就丢数。
    """
    return col_index in prior_cols


# source 桶名 -> B0 表名。用来从 B0Guide 里找回**那一张表**的列期间语义。
_SOURCE_TABLE_NAMES = {
    "balance_sheet": "资产负债表",
    "income_statement": "利润表",
    "cashflow": "现金流量表",
}

# 「本期」那一侧的期间标准名 —— 和 _PRIOR_PERIOD_STANDARD_NAMES 互补
_CURRENT_PERIOD_STANDARD_NAMES = frozenset(
    {"end_balance", "current_amount", "amount"})


def _table_guide_for(guide, source: str, use_parent: bool):
    """找出 source 桶对应的那份 TableGuide —— 为了拿它的列期间语义。

    找不到返回 None，让调用方自己决定怎么办，而不是从别处借一个
    看着像的列号。
    """
    want_name = _SOURCE_TABLE_NAMES.get(source)
    want_type = "母公司报表" if use_parent else "合并报表"
    for t in getattr(guide, "tables", None) or []:
        if t.table_name == want_name and t.report_type == want_type:
            return t
    return None


def _current_period_cell(columns: dict, table_guide) -> Optional[str]:
    """这一行里**本期/期末**那一格的值。

    旧代码写死 `for key in ("col_2", "col_3", "col_1")`，注释说 col_2 是
    "本期/期末" —— 那只在 项目|附注|期末|期初 这种 4 列布局下成立。
    3 列表 (项目|期末|期初) 里 col_2 是**期初**，于是 Monetary_Funds /
    Inventory 这类关键合计字段被整批覆盖成去年的数，而且是无条件覆盖：
    连 B1 刚按正确坐标取到的值也一并盖掉。

    现在按表头给出的期间语义挑列。认不出语义时退回老顺序 ——
    不知道不等于错，但无论如何都跳过已知的上期列。
    """
    prior = _prior_period_columns(table_guide)
    for col in getattr(table_guide, "columns", None) or []:
        if col.index in prior or col.standard_name not in _CURRENT_PERIOD_STANDARD_NAMES:
            continue
        v = columns.get(f"col_{col.index}", "")
        if v and not _is_note_ref(v):
            return v
    # 拿不到期间语义: 退回老顺序, 但别再碰已知的上期列
    for key in ("col_2", "col_3", "col_1"):
        if int(key[4:]) in prior:
            continue
        v = columns.get(key, "")
        if v and not _is_note_ref(v):
            return v
    return _find_numeric_column(columns, prior)


def _extract_value_fallback(
    rows: list[RawTableRow], fm: FieldMapping, guide,
) -> Optional[float]:
    """坐标取数降级 (索引未命中时)

    坐标是 LLM 给的，可能是猜的，所以要先**验证**它落在真能解析出数的
    单元格上。指到科目名列 / 文字说明列 / 上期列时就地在这一行里找
    本期数值列，而不是把字段判成不存在 —— 后者是静默丢数。

    `guide` 是必填的：它带的那份列期间语义是守卫的唯一依据。
    给个 `guide=None` 的默认值看着"更宽容", 实际是让守卫**恒为假** ——
    传漏了不报错, 只是安静地不再拦上期列。那正是本项目一直在犯的毛病。
    """
    row_index = fm.row_index
    col_index = fm.col_index
    if not (0 <= row_index < len(rows)):
        return None
    cols = rows[row_index].columns
    prior_cols = _prior_period_columns(guide)
    val_str = cols.get(f"col_{col_index}", "")
    if val_str and not _is_prior_period(prior_cols, col_index):
        parsed = _parse_number(val_str)
        if parsed is not None:
            return parsed
    # 坐标不可信: 退回"这一行里第一个本期数值列"
    return _parse_number(_find_numeric_column(cols, prior_cols) or "")


# ═══════════════════════════════════════════════
# 列式提取 (新增) 
# ═══════════════════════════════════════════════

def _extract_column_major(
    rows: list[RawTableRow], guide,
    bs: dict, pl: dict, cf: dict, table_name: str,
):
    """列式表格: 科目名在行、年份/类型在列"""
    multiplier = _unit_to_multiplier(guide.overall_unit)

    for fm in guide.field_mappings:
        for row in rows:
            row_text = " ".join(str(v) for v in row.columns.values())
            if fm.raw_name in row_text:
                val_col = 1 if fm.col_index == 0 else fm.col_index
                val_str = row.columns.get(f"col_{val_col}", "")
                if not val_str:
                    val_str = _find_numeric_column(row.columns)
                if val_str:
                    parsed = _parse_number(val_str)
                    if parsed is not None:
                        d_value = Decimal(str(parsed))
                        value_in_yuan = (d_value * multiplier).quantize(_MONEY_QUANTIZE, rounding=ROUND_HALF_UP)
                        if fm.is_negative:
                            value_in_yuan = -abs(value_in_yuan)
                        field = FinancialField(
                            standard_name=fm.standard_name, raw_name=fm.raw_name,
                            value=float(value_in_yuan),
                            original_unit=fm.unit or guide.overall_unit,
                            report_type=guide.report_type,
                        )
                        _assign_field(field, table_name, bs, pl, cf)
                break


# ═══════════════════════════════════════════════
# 公共工具
# ═══════════════════════════════════════════════

def _assign_field(field: FinancialField, table_name: str, bs: dict, pl: dict, cf: dict):
    if table_name in ("资产负债表",):
        bs[field.standard_name] = field
    elif table_name in ("利润表",):
        pl[field.standard_name] = field
    elif table_name in ("现金流量表",):
        cf[field.standard_name] = field


def _is_note_ref(text) -> bool:
    """判断是否为附注引用（如"七、1""五、10"），不是数值列"""
    t = str(text).strip()
    return bool(re.match(r'^[一二三四五六七八九十]+[、.，,]\s*\d+$', t))


def _find_numeric_column(
    columns: dict[str, str], skip: frozenset[int] = frozenset(),
) -> Optional[str]:
    """这一行里第一个像数值的列; `skip` 里的列号跳过。

    非 `col_N` 形状的键不会被跳过 (也就不会被误伤)。
    """
    for key, val in columns.items():
        if isinstance(key, str) and key.startswith("col_") and key[4:].isdigit():
            if int(key[4:]) in skip:
                continue
        if _is_note_ref(val):
            continue
        cleaned = str(val).replace(",", "").replace(" ", "").replace("%", "")
        if cleaned and any(c.isdigit() for c in cleaned):
            return str(val)
    return None


def _parse_number(text: str) -> Optional[float]:
    """解析数字: 支持千分位、括号负数、百分比、连字符负数"""
    if not text:
        return None
    text = str(text).strip()
    is_pct = text.endswith("%")
    if is_pct:
        text = text[:-1]
    negative = False
    if (text.startswith("(") and text.endswith(")")) or (text.startswith(" (") and text.endswith(") ")):
        negative = True
        text = text[1:-1]
    if text.startswith("-") or text.startswith("–") or text.startswith("—"):
        negative = True
        text = text[1:]
    text = text.replace(",", "").replace(" ", "").strip()
    try:
        value = float(text)
    except ValueError:
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
    if abs(value) > 1e15:
        logger.warning(f"数值异常: {text} -> {value}")
        return None
    return value


def _unit_to_multiplier(unit: str) -> Decimal:
    return {
        "元": Decimal("1"), "千元": Decimal("1000"), "万元": Decimal("10000"),
        "亿元": Decimal("100000000"), "百万元": Decimal("1000000"), "元/股": Decimal("1"),
    }.get(unit, Decimal("1"))


# ═══════════════════════════════════════════════
# 代码级补充提取 (对 LLM 遗漏的字段，直接从原始行匹配)
# ═══════════════════════════════════════════════

_ALL_KNOWN_FIELDS: list[dict] | None = None


def _load_all_known_fields() -> list[dict]:
    import yaml
    from pathlib import Path
    global _ALL_KNOWN_FIELDS
    if _ALL_KNOWN_FIELDS is not None:
        return _ALL_KNOWN_FIELDS
    map_path = Path(__file__).resolve().parent.parent.parent / "config" / "financial_fields.yaml"
    _ALL_KNOWN_FIELDS = []
    if map_path.exists():
        with open(map_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        for std_name, info in (data.get("fields", {}) if isinstance(data, dict) else {}).items():
            variants = info.get("chinese_variants", [])
            ftype = info.get("type", "")
            target = "income_statement" if ftype == "profit_loss" else "balance_sheet" if ftype == "balance_sheet" else "cashflow"
            _ALL_KNOWN_FIELDS.append({"standard_name": std_name, "variants": variants, "target": target})
    return _ALL_KNOWN_FIELDS


def _extract_all_known_fields(raw_doc, guide, bs, pl, cf):
    from decimal import Decimal, ROUND_HALF_UP
    all_known = _load_all_known_fields()
    all_rows = []
    for row in raw_doc.financial_data.balance_sheet:
        all_rows.append(("balance_sheet", row))
    for row in raw_doc.financial_data.income_statement:
        all_rows.append(("income_statement", row))
    for row in raw_doc.financial_data.cashflow_statement:
        all_rows.append(("cashflow", row))
    existing = set(bs.keys()) | set(pl.keys()) | set(cf.keys())
    overall_unit = guide.tables[0].overall_unit if guide.tables else "元"
    multiplier = {"元": 1, "千元": 1000, "万元": 10000, "亿元": 100000000, "百万元": 1000000}.get(overall_unit, 1)
    money_quantize = Decimal("0.01")
    added = 0
    for field_info in all_known:
        std_name = field_info["standard_name"]
        if std_name in existing:
            continue
        for source_type, row in all_rows:
            row_text = " ".join(str(v) for v in row.columns.values())
            if _is_sub_item_row(row_text):
                continue
            matched = any(v in row_text for v in field_info["variants"])
            if not matched:
                continue
            val_str = None
            for val in row.columns.values():
                if _is_note_ref(val):
                    continue
                cleaned = str(val).replace(",", "").replace(" ", "").replace("%", "")
                if cleaned and any(c.isdigit() for c in cleaned):
                    val_str = str(val)
                    break
            if not val_str:
                continue
            try:
                value = float(val_str.replace(",", "").replace(" ", "").replace("%", "").replace("(", "-").replace(")", ""))
            except ValueError:
                continue
            if multiplier != 1:
                value = value * multiplier
            from schemas.financial import FinancialField
            target_dict = {"balance_sheet": bs, "income_statement": pl, "cashflow": cf}[field_info["target"]]
            target_dict[std_name] = FinancialField(
                standard_name=std_name, raw_name=field_info["variants"][0],
                value=float(Decimal(str(value)).quantize(money_quantize, rounding=ROUND_HALF_UP)),
                original_unit=overall_unit, report_type=guide.tables[0].report_type if guide.tables else "合并报表",
            )
            existing.add(std_name)
            added += 1
            break
    if added:
        import logging
        logging.getLogger(__name__).info(f"代码级补充提取: 新增 {added} 个字段")
    return bs, pl, cf


# ═══════════════════════════════════════════════
# 关键合计字段精确提取 (确定性，不依赖 LLM)
# ═══════════════════════════════════════════════

# (标准字段名, 源表, 科目列精确候选)
_KEY_TOTALS: list[tuple[str, str, list[str]]] = [
    ("Total_Assets", "balance_sheet", ["资产总计", "资产总额"]),
    ("Total_Liabilities", "balance_sheet", ["负债合计"]),
    ("Equity_Total", "balance_sheet", ["所有者权益(或股东权益)合计", "所有者权益合计"]),
    ("Monetary_Funds", "balance_sheet", ["货币资金"]),
    ("Inventory", "balance_sheet", ["存货"]),
    ("Revenue_Total", "income_statement", ["营业收入", "营业总收入"]),
    ("Net_Profit", "income_statement", ["净利润"]),
    ("Cash_Flow_Op", "cashflow", ["经营活动产生的现金流量净额", "经营活动现金流量净额"]),
]


def _extract_key_totals(raw_doc, guide, bs, pl, cf, use_parent: bool = False):
    """对关键合计字段做精确科目名提取，缺失时补上

    用"科目列(col_0)精确等于候选名 + 取本期数值列"，
    避免 B0 漏映射或子串匹配误命中"流动负债合计"等细分行。
    本期列由表头的期间语义定（见 _current_period_cell）——
    不是写死的 col_2。
    use_parent=True 时对母公司报表桶提取（供 B+ 母子资金分离度）。
    """
    from decimal import Decimal, ROUND_HALF_UP
    fd = raw_doc.financial_data
    rows_map = {
        "balance_sheet": list(fd.parent_balance_sheet if use_parent else fd.balance_sheet),
        "income_statement": list(fd.parent_income_statement if use_parent else fd.income_statement),
        "cashflow": list(fd.parent_cashflow_statement if use_parent else fd.cashflow_statement),
    }
    target_map = {"balance_sheet": bs, "income_statement": pl, "cashflow": cf}
    money_quantize = Decimal("0.01")
    overall_unit = guide.tables[0].overall_unit if guide.tables else "元"
    report_type = "母公司报表" if use_parent else (guide.tables[0].report_type if guide.tables else "合并报表")
    added = 0

    for std_name, source, variants in _KEY_TOTALS:
        # 总是用精确提取覆盖: B0 可能把"负债合计"子串误匹配到"流动负债合计"行导致值错，
        # 科目列精确匹配(candidates)是确定性的，优先于 LLM 映射。
        # 但"覆盖"只能覆盖**值**, 不能覆盖**期间** —— 取哪一列得按表头语义来。
        table_guide = _table_guide_for(guide, source, use_parent)
        for row in rows_map[source]:
            col0 = str(row.columns.get("col_0", "")).strip()
            if col0 not in variants:
                continue
            val_str = _current_period_cell(row.columns, table_guide)
            value = _parse_number(val_str) if val_str else None
            if value is not None:
                target_map[source][std_name] = FinancialField(
                    standard_name=std_name, raw_name=variants[0],
                    value=float(Decimal(str(value)).quantize(money_quantize, rounding=ROUND_HALF_UP)),
                    original_unit=overall_unit, report_type=report_type,
                )
                added += 1
            break
    if added:
        logger.info(f"关键合计字段精确提取: 新增 {added} 个")
    return bs, pl, cf


# ═══════════════════════════════════════════════
# 交叉验证 (会计恒等式自检)
# ═══════════════════════════════════════════════

def _cross_validate_and_fix(bs: dict[str, FinancialField]) -> dict[str, FinancialField]:
    """用会计恒等式验证提取质量，发现问题自动尝试修正

    检查项: 
    1. 资产 ~ 负债 + 权益 (误差 < 5%，理想 < 1%) 
    2. 关键字段是否存在
    3. 数值是否异常 (负资产等) 

    修正策略: 
    - 如果总资产缺失但负债+权益都有 -> 推算资产
    - 如果等式偏差在 1-5% -> 记录日志但不阻断
    - 如果偏差 > 5% -> 标记可能单位换算错误
    """
    assets = bs.get("Total_Assets")
    liabilities = bs.get("Total_Liabilities")
    equity = bs.get("Equity_Total")

    # 关键字段缺失检查
    missing = []
    if not assets:
        missing.append("Total_Assets")
    if not liabilities:
        missing.append("Total_Liabilities")
    if not equity:
        missing.append("Equity_Total")

    # 如果有两个字段，推算第三个
    if missing and len(missing) == 1:
        if "Total_Assets" in missing and liabilities and equity:
            d_sum = Decimal(str(liabilities.value)) + Decimal(str(equity.value))
            bs["Total_Assets"] = FinancialField(
                standard_name="Total_Assets", raw_name="(推算)",
                value=float(d_sum.quantize(Decimal("0.01"))),
                original_unit="元", report_type="合并报表",
            )
            logger.info(f"交叉验证自动推算: Total_Assets = {liabilities.value} + {equity.value}")
        elif "Total_Liabilities" in missing and assets and equity:
            d_diff = Decimal(str(assets.value)) - Decimal(str(equity.value))
            bs["Total_Liabilities"] = FinancialField(
                standard_name="Total_Liabilities", raw_name="(推算)",
                value=float(d_diff.quantize(Decimal("0.01"))),
                original_unit="元", report_type="合并报表",
            )
        elif "Equity_Total" in missing and assets and liabilities:
            d_diff = Decimal(str(assets.value)) - Decimal(str(liabilities.value))
            bs["Equity_Total"] = FinancialField(
                standard_name="Equity_Total", raw_name="(推算)",
                value=float(d_diff.quantize(Decimal("0.01"))),
                original_unit="元", report_type="合并报表",
            )

    # 重新获取 (可能已被修正) 
    assets = bs.get("Total_Assets")
    liabilities = bs.get("Total_Liabilities")
    equity = bs.get("Equity_Total")

    # 等式验证
    if assets and liabilities and equity:
        d_assets = Decimal(str(assets.value))
        d_liabilities = Decimal(str(liabilities.value))
        d_equity = Decimal(str(equity.value))
        d_right = d_liabilities + d_equity

        if d_assets != 0:
            deviation = abs(d_assets - d_right) / d_assets
            if deviation > Decimal("0.05"):
                logger.warning(
                    f"交叉验证异常: 资产={d_assets}, 负债+权益={d_right}, "
                    f"偏差={float(deviation)*100:.1f}% (可能单位换算错误或数据提取不完整) "
                )
            elif deviation > Decimal("0.01"):
                logger.info(
                    f"交叉验证: 偏差={float(deviation)*100:.2f}%，在可接受范围内"
                )

    # 数值合理性检查
    for field_name in ["Total_Assets", "Monetary_Funds", "Inventory"]:
        field = bs.get(field_name)
        if field and field.value < 0:
            logger.warning(f"交叉验证异常: {field_name} 为负数 ({field.value})，可能提取错误")

    return bs
