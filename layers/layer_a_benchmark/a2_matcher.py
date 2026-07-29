"""A2层：财务数字画像匹配同行——纯代码向量相似度

职责：
- 从同花顺行业分类 CSV 获取二级行业同行池
- 通过 akshare 在线获取同行财务数据（内存缓存，不落盘）
- 5 年财务数据 → 稳健标准化 → 逐维中位数合并 → 6 维连续值向量
- 余弦相似度排序 → Top 5（E层展示）+ Top 20%（C层MAD基准）

设计约束：
- 纯代码实现，不涉及任何 LLM 调用
- 数据通过 akshare 实时获取 + 内存缓存，不写磁盘文件
- 不再经过离散等级中转，原始连续值直接标准化后做余弦相似度
"""

import csv
import logging
import math
import statistics
from pathlib import Path
from typing import Optional

from schemas.tags import CompanyTags, FinancialProfile, FINANCIAL_DIMENSIONS
from schemas.benchmark import Benchmark, PeerCompany, IndustryProfile

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────
# 路径常量
# ────────────────────────────────────────────
_INDUSTRY_CSV_PATH = Path("data/industry/thf_industry_classification.csv")

# 6 维特征顺序（与 FINANCIAL_DIMENSIONS 一致）
_DIMENSION_NAMES = FINANCIAL_DIMENSIONS  # 用全局统一顺序

# 维度名 → akshare 列名映射
_DIMENSION_TO_COLUMN = {
    "毛利率水平": "毛利率", "净利率水平": "净利率",
    "总资产周转率": "总资产周转率", "资产负债率": "资产负债率",
    "研发费用率": "研发费用率", "销售费用率": "销售费用率",
}

# akshare 返回的字段名映射（如果列名不同，在此修改）
_AKSHARE_COLUMN_MAP = {
    "毛利率": "毛利率", "净利率": "净利率",
    "总资产周转率": "总资产周转率", "资产负债率": "资产负债率",
    "研发费用率": "研发费用率", "销售费用率": "销售费用率",
}

_PEER_COUNT_MIN = 5
_TOP_PERCENT = 0.2


# ────────────────────────────────────────────
# 内存缓存（仅当前会话有效）
# ────────────────────────────────────────────
# key=stock_code, value=list[dict]（每元素含 6 个财务指标 + year）
_FINANCIAL_CACHE: dict[str, list[dict]] = {}


# ────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────

def run_matching(tags: CompanyTags) -> Benchmark:
    """匹配相似企业并计算基准

    流程（两遍扫描，砍掉等级中转）：
        1. 从硬标签确定二级行业，获取同行池
        2. 一次性拉取目标 + 全部同行的原始财务数据
        3. 计算 6 维稳健标准化统计量（median / IQR）
        4. 标准化目标公司数据 → 中位数合并 → 6 维连续值向量
        5. 标准化每家同行数据 → 中位数合并 → 6 维连续值向量
        6. 余弦相似度排序 → Top 5 + Top 20%（MAD 基准池）

    Args:
        tags: A1 层输出的 CompanyTags
              （含 hard_tags；financial_profile 由本函数从 akshare 计算）

    Returns:
        Benchmark（同行列表 + 横向中位数）
    """
    stock_code = tags.stock_code
    company_name = tags.company_name

    # 从硬标签提取二级行业
    industry_name = _extract_industry_level2(tags.hard_tags)
    if not industry_name:
        logger.warning("无法确定二级行业，返回空基准")
        return _empty_benchmark(tags)

    # 获取同行池
    peers = _load_peer_pool(industry_name)
    if not peers:
        logger.warning(f"行业 [{industry_name}] 在 CSV 中无数据")
        return _empty_benchmark(tags, industry_name)

    peer_count = len(peers)
    logger.info(f"行业 [{industry_name}] 共 {peer_count} 家同行")

    # ── Phase 1: 拉取全部原始数据 ──
    raw_data = _fetch_all_financial_data(stock_code, peers)
    if stock_code not in raw_data or not raw_data[stock_code]:
        logger.warning(f"目标公司 {company_name}({stock_code}) 无财务数据")
        return _empty_benchmark(tags, industry_name)

    # ── Phase 2: 计算标准化统计量 ──
    all_values: list[list[float]] = []
    for code, year_data in raw_data.items():
        for yr in year_data:
            raw_vec = _extract_raw_values(yr)
            if raw_vec is not None:
                all_values.append(raw_vec)

    if not all_values:
        logger.warning("同行池无有效财务数据")
        return _empty_benchmark(tags, industry_name)

    stats = _compute_robust_stats(all_values)
    logger.info(f"稳健标准化统计量计算完成（{len(all_values)} 个样本点）")

    # ── Phase 3: 标准化目标公司 ──
    target_year_data = raw_data[stock_code]
    target_vec = _normalize_merge_vector(target_year_data, stats)
    tags.financial_profile = FinancialProfile(values=target_vec)

    # ── Phase 4: 标准化同行 ──
    peer_vectors = _build_peer_vectors_from_raw(raw_data, peers, stats)
    if not peer_vectors:
        logger.warning("未能获取同行财务数据")
        return _empty_benchmark(tags, industry_name)

    # ── Phase 5: 余弦相似度排序 ──
    scored = _cosine_rank(target_vec, peer_vectors)

    # 双池
    top5 = scored[:5]
    mad_size = max(math.ceil(peer_count * _TOP_PERCENT), _PEER_COUNT_MIN)
    mad_pool = scored[:mad_size]
    if peer_count < 10:
        mad_pool = top5

    # 横向基准
    peer_median = _calc_peer_median(mad_pool)
    logger.info(f"MAD 基准池 {len(mad_pool)} 家")

    return Benchmark(
        industry=IndustryProfile(
            industry_name=industry_name,
            hard_tag_system="同花顺二级行业",
        ),
        peer_median=peer_median,
        historical_mean={},
        peer_companies=[
            PeerCompany(
                name=p["name"],
                stock_code=p["stock_code"],
                similarity_score=p.get("similarity_score", 0.0),
                financials=p.get("financials", {}),
            )
            for p in top5
        ],
    )


# ────────────────────────────────────────────
# 行业池（CSV 查表）
# ────────────────────────────────────────────

_INDUSTRY_CACHE: dict[str, dict] | None = None
_INDUSTRY_LIST: list[dict] | None = None


def _load_industry_csv():
    """惰性加载行业分类 CSV"""
    global _INDUSTRY_CACHE, _INDUSTRY_LIST
    if _INDUSTRY_CACHE is not None:
        return

    if not _INDUSTRY_CSV_PATH.exists():
        _INDUSTRY_CACHE, _INDUSTRY_LIST = {}, []
        return

    import io
    with open(_INDUSTRY_CSV_PATH, "rb") as f:
        text = f.read().decode("utf-8-sig")

    cache, rows = {}, []
    for row in csv.DictReader(io.StringIO(text)):
        code = row["股票代码"].strip()
        code_norm = code.split(".")[0]
        entry = {
            "code": code, "name": row["股票简称"].strip(),
            "level1": row["所属同花顺一级行业"].strip(),
            "level2": row["所属同花顺二级行业"].strip(),
            "level3": row["所属同花顺三级行业"].strip(),
        }
        cache[code_norm] = cache[code] = entry
        rows.append(entry)

    _INDUSTRY_CACHE, _INDUSTRY_LIST = cache, rows
    logger.info(f"A2 加载行业 CSV: {len(rows)} 只股票")


def _extract_industry_level2(hard_tags) -> str:
    """从硬标签中提取二级行业"""
    for ht in hard_tags:
        if ht.system == "同花顺二级行业" and ht.value:
            return ht.value
    for ht in hard_tags:
        if ht.system == "同花顺三级行业" and ht.value:
            return _resolve_level2(ht.value)
    return ""


def _resolve_level2(level3: str) -> str:
    _load_industry_csv()
    if _INDUSTRY_LIST:
        for row in _INDUSTRY_LIST:
            if row["level3"] == level3:
                return row["level2"]
    return ""


def _load_peer_pool(industry_level2: str) -> list[dict]:
    """加载同一二级行业的所有公司"""
    _load_industry_csv()
    if not _INDUSTRY_LIST:
        return []
    return [row for row in _INDUSTRY_LIST if row["level2"] == industry_level2]


# ────────────────────────────────────────────
# 财务数据获取（akshare + 内存缓存）
# ────────────────────────────────────────────

def _fetch_financial_data(stock_code: str) -> list[dict]:
    """从 akshare 获取某公司近年财务数据，带内存缓存

    Returns:
        [{"year": "2022", "毛利率": 82.5, "净利率": 35.2, ...}, ...]
        按年份降序排列（最新的在前）
    """
    # 命中缓存
    if stock_code in _FINANCIAL_CACHE:
        return _FINANCIAL_CACHE[stock_code]

    norm_code = stock_code.split(".")[0]
    if norm_code in _FINANCIAL_CACHE:
        return _FINANCIAL_CACHE[norm_code]

    # 尝试 akshare
    try:
        import akshare as ak
        df = ak.stock_financial_analysis_indicator(symbol=norm_code, start_year="2021")

        if df is None or df.empty:
            _FINANCIAL_CACHE[stock_code] = []
            return []

        # 解析 DataFrame 为统一格式
        records = _parse_akshare_df(df, norm_code)
        _FINANCIAL_CACHE[stock_code] = records
        if norm_code != stock_code:
            _FINANCIAL_CACHE[norm_code] = records
        return records

    except ImportError:
        logger.warning("akshare 未安装，无法获取财务数据")
        _FINANCIAL_CACHE[stock_code] = []
        return []
    except Exception as e:
        logger.warning(f"akshare 获取 {norm_code} 失败: {e}")
        _FINANCIAL_CACHE[stock_code] = []
        return []


def _parse_akshare_df(df, stock_code: str) -> list[dict]:
    """将 akshare 返回的 DataFrame 解析为统一格式

    需要适配实际 akshare 返回的列名结构。
    当前实现假设典型的财务分析指标表格式。
    """
    import pandas as pd

    records = []
    col_map = _AKSHARE_COLUMN_MAP

    for _, row in df.iterrows():
        year = str(row.get("年份", row.get("报告期", "")))
        if not year:
            continue

        record = {"year": year[:4]}
        has_data = False
        for dim_name in _DIMENSION_NAMES:
            col_name = col_map.get(dim_name, "")
            val = None
            if col_name in row:
                try:
                    val = float(row[col_name])
                except (ValueError, TypeError):
                    pass
            record[col_name] = val
            if val is not None:
                has_data = True

        if has_data:
            records.append(record)

    return records


def _fetch_all_financial_data(
    target_code: str, peers: list[dict]
) -> dict[str, list[dict]]:
    """一次性拉取目标公司 + 全部同行的原始财务数据（利用内存缓存）"""
    result: dict[str, list[dict]] = {}

    # 目标公司
    target_data = _fetch_financial_data(target_code)
    if target_data:
        result[target_code] = target_data

    # 全部同行
    for peer in peers:
        code = peer["code"]
        data = _fetch_financial_data(code)
        if data:
            result[code] = data

    logger.info(f"已获取 {len(result)} 家公司的原始财务数据")
    return result


# ────────────────────────────────────────────
# 原始值提取 & 稳健标准化
# ────────────────────────────────────────────


def _extract_raw_values(financial_data: dict) -> Optional[list[float]]:
    """从一行 akshare 数据提取 6 维原始值（不经过离散化）

    Returns:
        [毛利率, 净利率, 周转率, 负债率, 研发率, 销售率]
        任一维为 None 时整体返回 None
    """
    values = []
    for dim_name in _DIMENSION_NAMES:
        col = _DIMENSION_TO_COLUMN.get(dim_name, dim_name)
        val = financial_data.get(col)
        if val is None:
            return None
        try:
            values.append(float(val))
        except (ValueError, TypeError):
            return None
    return values


def _compute_robust_stats(all_values: list[list[float]]) -> dict[str, list[float]]:
    """对 6 维数据分别计算中位数和 IQR（稳健标准化参数）

    Args:
        all_values: [[v1_dim0, v1_dim1, ...], [v2_dim0, ...], ...]

    Returns:
        {"medians": [m0, ..., m5], "iqrs": [iqr0, ..., iqr5]}
    """
    if not all_values:
        return {"medians": [0.0] * 6, "iqrs": [1.0] * 6}

    dim_count = 6
    medians, iqrs = [], []

    for dim in range(dim_count):
        vals = sorted(row[dim] for row in all_values if row is not None)
        if not vals:
            medians.append(0.0)
            iqrs.append(1.0)
            continue

        median = statistics.median(vals)
        medians.append(median)

        # IQR = Q3 - Q1
        n = len(vals)
        q1 = vals[n // 4] if n > 1 else vals[0]
        q3 = vals[(3 * n) // 4] if n > 1 else vals[-1]
        iqr = q3 - q1
        iqrs.append(max(iqr, 1e-10))  # 避免除零

    return {"medians": medians, "iqrs": iqrs}


def _robust_scale_vector(
    values: list[float], stats: dict[str, list[float]]
) -> list[float]:
    """用预先算好的统计量对 6 维向量做稳健标准化

    公式: (x - median) / IQR
    """
    result = []
    for i, v in enumerate(values):
        median = stats["medians"][i]
        iqr = stats["iqrs"][i]
        result.append((v - median) / iqr)
    return result


def _merge_continuous_values(value_arrays: list[list[float]]) -> list[float]:
    """多年连续值 → 逐维中位数合并"""
    if not value_arrays:
        return [0.0] * 6
    if len(value_arrays) == 1:
        return value_arrays[0]

    merged = []
    for dim_idx in range(6):
        vals = sorted(row[dim_idx] for row in value_arrays)
        merged.append(statistics.median(vals))
    return merged


def _normalize_merge_vector(
    year_data: list[dict], stats: dict[str, list[float]]
) -> list[float]:
    """对一年或多年的原始财务数据：提取 → 标准化 → 中位数合并

    Returns: 6 维连续值向量（稳健标准化后）
    """
    normalized = []
    for yr in year_data:
        raw = _extract_raw_values(yr)
        if raw is not None:
            normalized.append(_robust_scale_vector(raw, stats))

    if not normalized:
        return [0.0] * 6
    return _merge_continuous_values(normalized)


def _build_peer_vectors_from_raw(
    raw_data: dict[str, list[dict]],
    peers: list[dict],
    stats: dict[str, list[float]],
) -> list[dict]:
    """从已获取的原始数据构建同行向量"""
    result = []
    for peer in peers:
        code = peer["code"]
        year_data = raw_data.get(code)
        if not year_data:
            continue

        merged_vec = _normalize_merge_vector(year_data, stats)

        # 最新年份原始财务数据（用于展示）
        latest = year_data[0]
        financials = {}
        for dim in _DIMENSION_NAMES:
            col = _DIMENSION_TO_COLUMN.get(dim, dim)
            financials[col] = float(latest.get(col, 0) or 0)

        result.append({
            "stock_code": code,
            "name": peer["name"],
            "financials": financials,
            "numeric_vector": merged_vec,
            "similarity_score": 0.0,
        })

    return result


# ────────────────────────────────────────────
# 余弦相似度
# ────────────────────────────────────────────


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _cosine_rank(target_vec: list[float], peers: list[dict]) -> list[dict]:
    for p in peers:
        p["similarity_score"] = round(
            _cosine_similarity(target_vec, p["numeric_vector"]), 4
        )
    return sorted(peers, key=lambda p: -p["similarity_score"])


# ────────────────────────────────────────────
# 基准 & 空降级
# ────────────────────────────────────────────

def _calc_peer_median(peers: list[dict]) -> dict[str, float]:
    vals: dict[str, list[float]] = {}
    for p in peers:
        for ind, val in p.get("financials", {}).items():
            vals.setdefault(ind, []).append(float(val))
    return {ind: round(statistics.median(vs), 4) for ind, vs in vals.items() if vs}


def _empty_benchmark(tags: CompanyTags, industry: str = "") -> Benchmark:
    name = industry or _extract_industry_level2(tags.hard_tags) or "未知行业"
    return Benchmark(
        industry=IndustryProfile(industry_name=name, hard_tag_system="同花顺二级行业"),
        peer_median={}, historical_mean={}, peer_companies=[],
    )
