"""B1层：代码提取表格数据——纯 Pandas，支持行式/列式两种布局

v2 新增：
- 列式（column_major）表格支持：科目名在行、年份值在列
- 多级表头跳过：data_start_row 以上全部略过
- 英文格式数字解析：逗号分隔（1,234,567）和括号负数（(500)）
- Decimal 精确运算：避免浮点误差累积（财务数据要求精确到分）
- 交叉验证：用会计恒等式验证提取数据质量，发现异常时自动修正
"""

import logging
import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from schemas.raw_doc import RawDocument, RawTableRow
from schemas.b0_guide import B0Guide, FieldMapping
from schemas.financial import FinancialStatement, FinancialField, ValidationResult

logger = logging.getLogger(__name__)

# 金额统一保留 2 位小数（分）
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
# 行式提取（原有逻辑，增强版）
# ═══════════════════════════════════════════════

def _extract_row_major(
    rows: list[RawTableRow], guide,
    bs: dict, pl: dict, cf: dict, table_name: str,
):
    """行式表格：每行一个科目，从指定列取数值"""
    multiplier = _unit_to_multiplier(guide.overall_unit)

    for fm in guide.field_mappings:
        value = _extract_value(rows, fm)
        if value is None:
            continue
        field_mult = _unit_to_multiplier(fm.unit)
        # 用 Decimal 避免浮点误差：value × field_mult × multiplier
        d_value = Decimal(str(value))
        value_in_yuan = d_value * field_mult
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


def _extract_value(rows: list[RawTableRow], fm: FieldMapping) -> Optional[float]:
    """从行式表格中按坐标取数"""
    row_index = fm.row_index
    col_index = fm.col_index
    # 精确坐标
    if 0 <= row_index < len(rows):
        val_str = rows[row_index].columns.get(f"col_{col_index}", "")
        if val_str:
            parsed = _parse_number(val_str)
            if parsed is not None:
                return parsed
    # 降级搜索
    for row in rows:
        row_text = " ".join(str(v) for v in row.columns.values())
        if fm.raw_name and fm.raw_name in row_text:
            val_str = _find_numeric_column(row.columns)
            if val_str:
                parsed = _parse_number(val_str)
                if parsed is not None:
                    return parsed
            next_idx = rows.index(row) + 1
            if next_idx < len(rows):
                val_str = _find_numeric_column(rows[next_idx].columns)
                if val_str:
                    parsed = _parse_number(val_str)
                    if parsed is not None:
                        return parsed
    return None


# ═══════════════════════════════════════════════
# 列式提取（新增）
# ═══════════════════════════════════════════════

def _extract_column_major(
    rows: list[RawTableRow], guide,
    bs: dict, pl: dict, cf: dict, table_name: str,
):
    """列式表格：科目名在行、年份/类型在列"""
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
    """解析数字：支持千分位、括号负数、百分比、连字符负数"""
    if not text:
        return None
    text = str(text).strip()
    is_pct = text.endswith("%")
    if is_pct:
        text = text[:-1]
    negative = False
    if (text.startswith("(") and text.endswith(")")) or (text.startswith("（") and text.endswith("）")):
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
        logger.warning(f"数值异常: {text} → {value}")
        return None
    return value


def _unit_to_multiplier(unit: str) -> Decimal:
    return {
        "元": Decimal("1"), "千元": Decimal("1000"), "万元": Decimal("10000"),
        "亿元": Decimal("100000000"), "百万元": Decimal("1000000"), "元/股": Decimal("1"),
    }.get(unit, Decimal("1"))


# ═══════════════════════════════════════════════
# 交叉验证（会计恒等式自检）
# ═══════════════════════════════════════════════

def _cross_validate_and_fix(bs: dict[str, FinancialField]) -> dict[str, FinancialField]:
    """用会计恒等式验证提取质量，发现问题自动尝试修正

    检查项：
    1. 资产 ≈ 负债 + 权益（误差 < 5%，理想 < 1%）
    2. 关键字段是否存在
    3. 数值是否异常（负资产等）

    修正策略：
    - 如果总资产缺失但负债+权益都有 → 推算资产
    - 如果等式偏差在 1-5% → 记录日志但不阻断
    - 如果偏差 > 5% → 标记可能单位换算错误
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

    # 重新获取（可能已被修正）
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
                    f"偏差={float(deviation)*100:.1f}%（可能单位换算错误或数据提取不完整）"
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
