"""A2层：财务数字画像匹配同行——纯代码向量相似度

职责：
- 从同花顺行业分类 CSV 获取二级行业同行池
- 通过 akshare 在线获取同行财务数据（内存缓存，不落盘）
- 5 年财务数据 → 逐维中位数合并 → 6 维数值向量
- 余弦相似度排序 → Top 5（E层展示）+ Top 20%（C层MAD基准）

设计约束：
- 纯代码实现，不涉及任何 LLM 调用
- 数据通过 akshare 实时获取 + 内存缓存，不写磁盘文件
"""

import csv
import logging
import math
import statistics
from pathlib import Path
from typing import Optional

from schemas.tags import CompanyTags, FinancialProfile
from schemas.benchmark import Benchmark, PeerCompany, IndustryProfile

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────
# 路径常量
# ────────────────────────────────────────────
_INDUSTRY_CSV_PATH = Path("data/industry/thf_industry_classification.csv")

# 6 维特征顺序（与 FINANCIAL_DIMENSIONS 一致）
_DIMENSION_NAMES = [
    "毛利率水平", "净利率水平", "总资产周转率",
    "资产负债率", "研发费用率", "销售费用率",
]

# CSV → 内部字段名映射
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

    Args:
        tags: A1 层输出的 CompanyTags
              （含 hard_tags；financial_profile 由本函数从 akshare 计算）

    Returns:
        Benchmark（同行列表 + 横向中位数）
    """
    # 从 akshare 实际数据计算目标公司的财务画像
    if not tags.financial_profile:
        target_profile = _compute_target_profile(tags.stock_code, tags.company_name)
        if target_profile:
            tags.financial_profile = target_profile
        else:
            logger.warning("目标公司无财务数字画像，返回空基准")
            return _empty_benchmark(tags)

    target_vec = tags.financial_profile.as_numeric

    # 从硬标签提取二级行业
    industry_name = _extract_industry_level2(tags.hard_tags)
    if not industry_name:
        logger.warning("无法确定二级行业，返回空基准")
        return _empty_benchmark(tags)

    # 获取同行池（同二级行业的所有公司代码）
    peers = _load_peer_pool(industry_name)
    if not peers:
        logger.warning(f"行业 [{industry_name}] 在 CSV 中无数据")
        return _empty_benchmark(tags, industry_name)

    peer_count = len(peers)
    logger.info(f"行业 [{industry_name}] 共 {peer_count} 家同行")

    # 通过 akshare 获取财务数据（内存缓存）
    peer_vectors = _fetch_and_compute_vectors(peers)
    if not peer_vectors:
        logger.warning("未能获取同行财务数据")
        return _empty_benchmark(tags, industry_name)

    # 余弦相似度排序
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


def _fetch_and_compute_vectors(peers: list[dict]) -> list[dict]:
    """对每家公司获取数据并计算等级向量"""
    result = []
    for peer in peers:
        code = peer["code"]
        year_data = _fetch_financial_data(code)
        if not year_data:
            continue

        # 每行 → 等级数组
        level_arrays = [_compute_level_array(yr) for yr in year_data]
        merged = _merge_levels_by_median(level_arrays)
        numeric_vec = FinancialProfile(levels=merged).as_numeric

        # 最新年份的财务数据
        latest = year_data[0] if year_data else {}
        financials = {}
        for dim in _DIMENSION_NAMES:
            col = _DIMENSION_TO_COLUMN.get(dim, dim)
            financials[col] = float(latest.get(col, 0) or 0)

        result.append({
            "stock_code": code,
            "name": peer["name"],
            "financials": financials,
            "level_array": merged,
            "numeric_vector": numeric_vec,
            "similarity_score": 0.0,
        })

    return result


# ────────────────────────────────────────────
# 等级映射 & 余弦相似度
# ────────────────────────────────────────────

# 等级阈值（从 YAML 惰性加载）
_LEVEL_THRESHOLDS: dict[str, list[tuple[str, float]]] | None = None


def _load_thresholds():
    global _LEVEL_THRESHOLDS
    if _LEVEL_THRESHOLDS is not None:
        return _LEVEL_THRESHOLDS

    config_path = Path("config/industry_tags.yaml")
    if not config_path.exists():
        _LEVEL_THRESHOLDS = {}
        return _LEVEL_THRESHOLDS

    import yaml
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    dims = config.get("financial_profile", {}).get("dimensions", [])
    th = {}
    for d in dims:
        name = d["name"]
        th[name] = sorted(d["thresholds"].items(), key=lambda x: -x[1])
    _LEVEL_THRESHOLDS = th
    return th


def _value_to_level(value, thresholds) -> str:
    for level, threshold in thresholds:
        if float(value) >= threshold:
            return level
    return thresholds[-1][0] if thresholds else "中"


def _compute_level_array(financial_data: dict) -> list[str]:
    """一行财务数据 → 6 维等级数组"""
    thresholds = _load_thresholds()
    levels = []
    for dim_name in _DIMENSION_NAMES:
        col = _DIMENSION_TO_COLUMN.get(dim_name, dim_name)
        val = financial_data.get(col)
        dim_th = thresholds.get(dim_name, [])
        if val is None or not dim_th:
            levels.append("中")
        else:
            levels.append(_value_to_level(val, dim_th))
    return levels


def _merge_levels_by_median(level_arrays: list[list[str]]) -> list[str]:
    """多年等级 → 逐维中位数合并"""
    if not level_arrays:
        return ["中"] * 6
    if len(level_arrays) == 1:
        return level_arrays[0]

    from schemas.tags import LEVEL_TO_SCORE
    score_matrix = [
        [LEVEL_TO_SCORE.get(lv, 0.5) for lv in arr]
        for arr in level_arrays
    ]
    merged = []
    for dim_idx in range(6):
        vals = sorted(row[dim_idx] for row in score_matrix)
        median_val = statistics.median(vals)
        merged.append(_nearest_level(median_val))
    return merged


def _nearest_level(score: float) -> str:
    from schemas.tags import LEVEL_TO_SCORE
    best, best_dist = "中", float("inf")
    for level, s in LEVEL_TO_SCORE.items():
        d = abs(score - s)
        if d < best_dist:
            best_dist, best = d, level
    return best


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


def _compute_target_profile(stock_code: str, company_name: str) -> Optional[FinancialProfile]:
    """从 akshare 实际财务数据计算目标公司的 6 维等级画像

    复用同行计算逻辑：
    - _fetch_financial_data() → 5 年原始财务数据
    - _compute_level_array() → 每年 6 维等级
    - _merge_levels_by_median() → 逐维中位数合并
    """
    if not stock_code:
        logger.warning(f"目标公司 {company_name} 无股票代码")
        return None

    year_data = _fetch_financial_data(stock_code)
    if not year_data:
        logger.warning(f"akshare 未获取到 {company_name}({stock_code}) 的财务数据")
        return None

    level_arrays = [_compute_level_array(yr) for yr in year_data]
    merged = _merge_levels_by_median(level_arrays)
    return FinancialProfile(levels=merged)


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
