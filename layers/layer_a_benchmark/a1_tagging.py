"""A1层：纯代码查硬标签——同花顺行业分类 CSV

职责：
- 从同花顺行业分类 CSV 查表获取硬标签（三级+二级+一级行业）
- 两级降级：股票代码精确匹配 → 公司名模糊匹配
- CSV 查不到则返回空硬标签（该企业不在同花顺分类库中）
- 纯代码实现，不涉及任何 LLM 调用
- 财务数字画像由 A2 层从 akshare 实际财务数据计算
"""

import logging
from pathlib import Path

from schemas.tags import CompanyTags, HardTag
from schemas.raw_doc import RawDocument

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────
# 同花顺行业分类 CSV 查表（硬标签）
# ────────────────────────────────────────────

_INDUSTRY_CSV_PATH = Path("data/industry/thf_industry_classification.csv")
_INDUSTRY_CACHE: dict[str, dict] | None = None
_NAME_INDEX: list[tuple[str, dict]] | None = None


def _load_industry_csv() -> dict[str, dict]:
    """惰性加载同花顺行业分类 CSV"""
    global _INDUSTRY_CACHE, _NAME_INDEX
    if _INDUSTRY_CACHE is not None:
        return _INDUSTRY_CACHE

    import csv
    import io

    if not _INDUSTRY_CSV_PATH.exists():
        logger.warning(f"行业分类 CSV 不存在: {_INDUSTRY_CSV_PATH}")
        _INDUSTRY_CACHE = {}
        _NAME_INDEX = []
        return _INDUSTRY_CACHE

    with open(_INDUSTRY_CSV_PATH, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8-sig")

    reader = csv.DictReader(io.StringIO(text))
    required_cols = {"股票代码", "股票简称", "所属同花顺一级行业", "所属同花顺二级行业", "所属同花顺三级行业"}
    if not required_cols.issubset(reader.fieldnames or []):
        logger.warning(f"行业分类 CSV 列名不匹配: {reader.fieldnames}")
        _INDUSTRY_CACHE = {}
        _NAME_INDEX = []
        return _INDUSTRY_CACHE

    cache: dict[str, dict] = {}
    name_index: list[tuple[str, dict]] = []
    row_count = 0

    for row in reader:
        code = row["股票代码"].strip()
        code_norm = code.split(".")[0]
        entry = {
            "code": code,
            "name": row["股票简称"].strip(),
            "level1": row["所属同花顺一级行业"].strip(),
            "level2": row["所属同花顺二级行业"].strip(),
            "level3": row["所属同花顺三级行业"].strip(),
        }
        cache[code_norm] = entry
        cache[code] = entry
        name_index.append((entry["name"], entry))
        row_count += 1

    if row_count < 5000:
        logger.warning(f"行业分类 CSV 行数异常: {row_count}")

    _INDUSTRY_CACHE = cache
    _NAME_INDEX = name_index
    logger.info(f"已加载行业分类 CSV: {row_count} 只股票")
    return cache


def _csv_lookup_by_code(stock_code: str) -> dict | None:
    """按股票代码精确匹配"""
    if not stock_code:
        return None
    cache = _load_industry_csv()
    return cache.get(stock_code.strip())


def _csv_lookup_by_name(company_name: str) -> dict | None:
    """按公司名模糊匹配"""
    if not company_name or _NAME_INDEX is None:
        return None
    for short_name, entry in _NAME_INDEX:
        if short_name in company_name or company_name in short_name:
            return entry
    return None


def _csv_lookup_hard_tags(stock_code: str, company_name: str) -> list[HardTag] | None:
    """CSV 查表获取硬标签（三级+二级+一级）"""
    result = _csv_lookup_by_code(stock_code)
    if not result:
        result = _csv_lookup_by_name(company_name)
    if result:
        return [
            HardTag(system="同花顺三级行业", value=result["level3"]),
            HardTag(system="同花顺二级行业", value=result["level2"]),
            HardTag(system="同花顺一级行业", value=result["level1"]),
        ]
    return None


# ────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────

def run_tagging(raw_doc: RawDocument) -> CompanyTags:
    """读取公司信息，输出硬标签（纯代码，不调 LLM）

    流程：
        1. CSV 查硬标签（股票代码 → 公司名模糊匹配）
        2. CSV 查不到 → 返回空硬标签（不降级）
        3. 财务数字画像由 A2 层从 akshare 实际数据计算
    """
    company_name = raw_doc.metadata.get("company_name", "")
    stock_code = raw_doc.metadata.get("stock_code", "")

    # CSV 查硬标签
    csv_hard_tags = _csv_lookup_hard_tags(stock_code, company_name)
    if csv_hard_tags:
        logger.info(f"CSV 查到硬标签: {', '.join(f'{t.system}={t.value}' for t in csv_hard_tags)}")
        return CompanyTags(
            company_name=company_name,
            stock_code=stock_code,
            hard_tags=csv_hard_tags,
            financial_profile=None,  # A2 层会从 akshare 计算
        )

    # CSV 查不到，直接返回空标签
    logger.warning(f"CSV 未查到 {company_name}({stock_code})，该企业不在同花顺行业分类库中")
    return CompanyTags(
        company_name=company_name,
        stock_code=stock_code,
        hard_tags=[],
        financial_profile=None,
    )
