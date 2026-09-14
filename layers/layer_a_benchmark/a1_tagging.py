"""A1层: 纯代码查硬标签——申万行业分类 CSV

职责:
- 从申万行业分类 CSV 查表获取硬标签 (三级+二级+一级行业)
- 两级降级: 股票代码精确匹配 -> 公司名模糊匹配
- CSV 查不到则返回空硬标签 (该企业不在申万分类库中)
- 纯代码实现，不涉及任何 LLM 调用
- 财务数字画像由 A2 层从 akshare 实际财务数据计算

设计约束:
- **只发有值的标签。** 一家公司可能只有一级行业 (旧表里只到一级名),
  或者没有三级行业。缺的级别就不发标签, 而不是发一个 value="" 的空壳 ——
  空壳会让下游误以为"查到了", 按 system 名取到空串继续算下去。
"""

import logging
from pathlib import Path

from schemas.tags import CompanyTags, HardTag
from schemas.raw_doc import RawDocument

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────
# 申万行业分类 CSV 查表 (硬标签)
# ────────────────────────────────────────────

_INDUSTRY_CSV_PATH = Path("data/industry/sw_industry_classification.csv")
_INDUSTRY_CACHE: dict[str, dict] | None = None
_NAME_INDEX: list[tuple[str, dict]] | None = None


def _load_industry_csv() -> dict[str, dict]:
    """惰性加载申万行业分类 CSV"""
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
    required_cols = {"股票代码", "股票简称", "所属申万一级行业", "所属申万二级行业", "所属申万三级行业"}
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
            "level1": row["所属申万一级行业"].strip(),
            "level2": row["所属申万二级行业"].strip(),
            "level3": row["所属申万三级行业"].strip(),
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
    """按公司名模糊匹配

    这里必须先显式把 CSV 加载起来: 缓存是惰性的, 而按代码查在代码为空时
    会提前 return、根本不会碰缓存 —— 于是缺股票代码的公司走名称兜底时
    `_NAME_INDEX` 还是 None, 兜底静默失效。文档承诺的两级降级不能只在
    「代码存在但查不到」时才生效。
    """
    if not company_name:
        return None
    _load_industry_csv()
    if _NAME_INDEX is None:
        return None
    for short_name, entry in _NAME_INDEX:
        if short_name in company_name or company_name in short_name:
            return entry
    return None


def _csv_lookup_hard_tags(stock_code: str, company_name: str) -> list[HardTag] | None:
    """CSV 查表获取硬标签 (三级+二级+一级, 由细到粗)

    只发有值的级别。旧表里 5656 家公司只有一级行业, 它们就该只拿到一个标签;
    给缺失的级别发一个 value="" 的空壳, 只会让下游把"没查到"误读成"查到了"。
    """
    result = _csv_lookup_by_code(stock_code)
    if not result:
        result = _csv_lookup_by_name(company_name)
    if not result:
        return None

    tags: list[HardTag] = []
    for level, key in (("三级", "level3"), ("二级", "level2"), ("一级", "level1")):
        value = (result.get(key) or "").strip()
        if value:
            tags.append(HardTag(system=f"申万{level}行业", value=value))
    return tags


# ────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────

def run_tagging(raw_doc: RawDocument) -> CompanyTags:
    """读取公司信息，输出硬标签 (纯代码，不调 LLM)

    流程:
        1. CSV 查硬标签 (股票代码 -> 公司名模糊匹配)
        2. CSV 查不到 -> 返回空硬标签 (不降级)
        3. 财务数字画像由 A2 层从 akshare 实际数据计算
    """
    from pipeline.tracer import tracer

    company_name = raw_doc.company_overview.company_name or ""
    stock_code = raw_doc.company_overview.stock_code or ""

    csv_hard_tags = _csv_lookup_hard_tags(stock_code, company_name)

    # 三种"没拿到标签"要分开说 —— 它们的下游含义完全不同:
    #   None -> 表里根本没这家公司
    #   []   -> 表里有, 但那个行业名解析不出申万层级 (旧表遗留名称, 全表 9 只)
    if csv_hard_tags is None:
        logger.warning(f"CSV 未查到 {company_name}({stock_code})，该企业不在申万行业分类库中")
        tracer.milestone(
            "A1", "硬标签定位", "success",
            f"CSV 未查到 {company_name}({stock_code})，返回空标签（无兜底）",
        )
        return CompanyTags(company_name=company_name, stock_code=stock_code,
                           hard_tags=[], financial_profile=None)

    if not csv_hard_tags:
        logger.warning(f"CSV 查到 {company_name}({stock_code})，但其行业名无法解析成申万层级")
        tracer.milestone(
            "A1", "硬标签定位", "success",
            f"{company_name}({stock_code}) 的行业名无法解析成申万层级，返回空标签",
        )
        return CompanyTags(company_name=company_name, stock_code=stock_code,
                           hard_tags=[], financial_profile=None)

    detail = ", ".join(f"{t.system}={t.value}" for t in csv_hard_tags)
    logger.info(f"CSV 查到硬标签: {detail}")
    tracer.milestone("A1", "硬标签定位", "success", f"匹配成功: {detail}")
    return CompanyTags(
        company_name=company_name,
        stock_code=stock_code,
        hard_tags=csv_hard_tags,
        financial_profile=None,  # A2 层会从 akshare 计算
    )
