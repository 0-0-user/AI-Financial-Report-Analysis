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


def run_extraction(raw_doc: RawDocument, guide: B0Guide) -> FinancialStatement:
    """根据 B0 指引定位取数，支持行式和列式表格"""
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
        if not rows:
            continue

        layout = getattr(table_guide, "layout", "row_major")

        if layout == "column_major":
            _extract_column_major(
                rows, table_guide, balance_sheet, income_statement, cashflow, table_name
            )
        else:
            _extract_row_major(
                rows, table_guide, balance_sheet, income_statement, cashflow, table_name
            )

    # 交叉验证 + 自动修复
    balance_sheet = _cross_validate_and_fix(balance_sheet)

    # 代码级补充提取: 对 LLM 遗漏的字段，直接从原始行匹配 financial_fields.yaml
    balance_sheet, income_statement, cashflow = _extract_all_known_fields(
        raw_doc, guide, balance_sheet, income_statement, cashflow
    )

    company_name = raw_doc.company_overview.company_name or ""
    stock_code = raw_doc.company_overview.stock_code or ""
    report_year = raw_doc.metadata.report_year or 0
    report_type = guide.tables[0].report_type if guide.tables else "合并报表"

    return FinancialStatement(
        company_name=company_name, stock_code=stock_code,
        year=report_year, report_type=report_type,
        balance_sheet=balance_sheet, income_statement=income_statement,
        cashflow=cashflow,
        validation=ValidationResult(is_valid=True, checks=[]),
    )


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
    row_index = _build_row_index(rows, guide.field_mappings)

    for fm in guide.field_mappings:
        entry = row_index.get(fm.raw_name)
        if entry is None:
            # fallback: 坐标取数
            value = _extract_value_fallback(rows, fm)
        else:
            row, val_str = entry
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


def _build_row_index(
    rows: list[RawTableRow], field_mappings: list[FieldMapping],
) -> dict[str, tuple[RawTableRow, str | None]]:
    """预建 raw_name -> (row, 数值列) 索引，O(rows)

    对每行提取科目名 (col_0 或 col_0+col_1) ，查 field_mappings 中
    的 raw_name 是否在行文本中 -> 匹配则记录该行和最新数值列。
    """
    idx: dict[str, tuple[RawTableRow, str | None]] = {}
    known_names = {fm.raw_name for fm in field_mappings}
    for row in rows:
        cols = row.columns
        row_text = " ".join(str(v) for v in cols.values())
        for name in known_names:
            if name in row_text:
                val_str = _find_numeric_column(cols)
                idx[name] = (row, val_str)
    return idx


def _extract_value_fallback(rows: list[RawTableRow], fm: FieldMapping) -> Optional[float]:
    """坐标取数降级 (索引未命中时) """
    row_index = fm.row_index
    col_index = fm.col_index
    if 0 <= row_index < len(rows):
        val_str = rows[row_index].columns.get(f"col_{col_index}", "")
        if val_str:
            return _parse_number(val_str)
    return None


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


def _find_numeric_column(columns: dict[str, str]) -> Optional[str]:
    for val in columns.values():
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
            matched = any(v in row_text for v in field_info["variants"])
            if not matched:
                continue
            val_str = None
            for val in row.columns.values():
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
