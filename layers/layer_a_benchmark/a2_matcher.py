"""A2层：匹配模式相似的企业——纯代码匹配 + 算基准

职责：
- 硬标签强制锁定行业范围（如"白酒"行业只能从白酒同行中找）
- 软标签计算相似度（Jaccard + 加权匹配），找 3~5 家最相似同行
- 计算同行中位数（横向基准）和自身 5 年均值（纵向基准）

设计约束（架构文档要求）：
- 纯代码实现，不涉及任何 LLM 调用
- 基准数据从 data/benchmarks/ 读取
"""

import csv
import json
import logging
import statistics
from pathlib import Path
from typing import Optional

import yaml

from schemas.tags import CompanyTags, HardTag, SoftTag
from schemas.benchmark import Benchmark, PeerCompany, IndustryProfile

logger = logging.getLogger(__name__)

# 基准数据默认路径
DEFAULT_BENCHMARK_DIR = Path("data/benchmarks")
TAGS_CONFIG_PATH = Path("config/industry_tags.yaml")
PEER_COUNT_MIN = 3
PEER_COUNT_MAX = 5


# ────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────

def run_matching(
    tags: CompanyTags,
    benchmark_dir: Optional[Path] = None,
) -> Benchmark:
    """匹配相似企业并计算基准

    流程：
        1. 硬标签锁定行业范围 → 过滤出同行业的公司
        2. 软标签加权相似度 → 排序取 Top 3~5
        3. 计算同行中位数（横向基准）
        4. 提取该企业自身 5 年数据均值（纵向基准）

    Args:
        tags: A1 层输出的 CompanyTags
        benchmark_dir: 基准数据库目录，默认 data/benchmarks/

    Returns:
        Benchmark（同行公司列表 + 中位数 + 历史均值）
    """
    bm_dir = benchmark_dir or DEFAULT_BENCHMARK_DIR

    # 步骤1：加载全部基准数据
    all_peers = _load_benchmark_database(bm_dir)

    # 步骤2：用硬标签锁定行业
    hard_industry = _extract_primary_hard_tag(tags.hard_tags)
    same_industry = _filter_by_hard_tag(all_peers, hard_industry)

    if not same_industry:
        logger.warning(f"基准库中没有找到行业 [{hard_industry}] 的数据，返回空基准")
        return Benchmark(
            industry=IndustryProfile(
                industry_name=hard_industry,
                hard_tag_system="同花顺三级行业",
            ),
            peer_median={},
            historical_mean={},
            peer_companies=[],
        )

    # 步骤3：用软标签计算相似度并排序
    soft_tag_weights = _load_soft_tag_weights()
    scored_peers = _score_and_rank(same_industry, tags.soft_tags, soft_tag_weights)

    # 步骤4：取 Top 3~5
    top_peers = scored_peers[:PEER_COUNT_MAX]
    if len(top_peers) < PEER_COUNT_MIN and len(same_industry) >= PEER_COUNT_MIN:
        top_peers = same_industry[:PEER_COUNT_MIN]  # 相似度不够时放宽限制

    # 步骤5：计算横向基准（同行中位数）
    peer_median = _calc_peer_median(top_peers)

    # 步骤6：计算纵向基准（自身历史均值）
    historical_mean = _calc_historical_mean(all_peers, tags.stock_code)

    # 构建软标签摘要
    soft_summary = ", ".join(
        f"{t.dimension}={t.value}" for t in tags.soft_tags[:8]
    )

    return Benchmark(
        industry=IndustryProfile(
            industry_name=hard_industry,
            hard_tag_system="同花顺三级行业",
            soft_tag_summary=soft_summary,
        ),
        peer_median=peer_median,
        historical_mean=historical_mean,
        peer_companies=[
            PeerCompany(
                name=p["name"],
                stock_code=p["stock_code"],
                similarity_score=p.get("similarity_score", 0.0),
                financials=p.get("financials", {}),
            )
            for p in top_peers
        ],
    )


# ────────────────────────────────────────────
# 数据加载
# ────────────────────────────────────────────

def _load_benchmark_database(bm_dir: Path) -> list[dict]:
    """从基准目录加载全部公司财务数据

    支持格式：CSV（列含 stock_code, name, industry, year, indicator, value）
              或 JSON（每公司一个文件）
    """
    companies: dict[str, dict] = {}  # key = stock_code

    if not bm_dir.exists():
        logger.warning(f"基准数据目录不存在: {bm_dir}")
        return []

    # 尝试加载 CSV 格式
    csv_files = list(bm_dir.glob("*.csv"))
    for csv_path in csv_files:
        with open(csv_path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = row.get("stock_code", "")
                if not code:
                    continue
                if code not in companies:
                    companies[code] = {
                        "stock_code": code,
                        "name": row.get("name", row.get("company_name", "")),
                        "industry": row.get("industry", ""),
                        "hard_tags": [row.get("industry", "")],
                        "soft_tags": [],
                        "financials": {},
                        "historical": {},  # 按年份存储的历史数据
                    }
                # 累积财务指标
                indicator = row.get("indicator", "")
                try:
                    value = float(row.get("value", 0))
                except (ValueError, TypeError):
                    continue
                if indicator:
                    companies[code]["financials"][indicator] = value

    # 尝试加载 JSON 格式
    json_files = list(bm_dir.glob("*.json"))
    for json_path in json_files:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                for entry in data:
                    code = entry.get("stock_code", "")
                    if code and code not in companies:
                        companies[code] = entry
            elif isinstance(data, dict):
                code = data.get("stock_code", "")
                if code and code not in companies:
                    companies[code] = data

    return list(companies.values())


def _load_soft_tag_weights() -> dict[str, float]:
    """从 config/industry_tags.yaml 加载软标签匹配权重"""
    if not TAGS_CONFIG_PATH.exists():
        return {}
    with open(TAGS_CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config.get("matching_weights", {}).get("soft_tag_weights", {})


# ────────────────────────────────────────────
# 硬标签过滤
# ────────────────────────────────────────────

def _extract_primary_hard_tag(hard_tags: list[HardTag]) -> str:
    """提取主硬标签值（优先同花顺三级行业）"""
    for ht in hard_tags:
        if ht.system == "同花顺三级行业" and ht.value:
            return ht.value
    # 回退：取第一个非空的硬标签
    for ht in hard_tags:
        if ht.value:
            return ht.value
    return "未知行业"


def _filter_by_hard_tag(peers: list[dict], industry: str) -> list[dict]:
    """硬标签强制筛选：只保留同行业公司

    排除被分析公司自身（后面通过 stock_code 区分）
    """
    return [
        p for p in peers
        if industry in (p.get("industry", ""), p.get("hard_tags", []))
           or industry in str(p.get("hard_tags", []))
    ]


# ────────────────────────────────────────────
# 软标签相似度计算
# ────────────────────────────────────────────

def _score_and_rank(
    peers: list[dict],
    target_soft_tags: list[SoftTag],
    dimension_weights: dict[str, float],
) -> list[dict]:
    """用软标签计算相似度并排序（降序）"""
    scored = []
    for peer in peers:
        peer_soft_tags = peer.get("soft_tags", [])
        if isinstance(peer_soft_tags, list) and peer_soft_tags and isinstance(peer_soft_tags[0], str):
            # 如果软标签以字符串形式存储（如 "重资产"），做简单匹配
            score = _simple_string_similarity(target_soft_tags, peer_soft_tags, dimension_weights)
        elif isinstance(peer_soft_tags, list) and peer_soft_tags and isinstance(peer_soft_tags[0], dict):
            # 如果软标签以 dict 形式存储（如 {"dimension": "资产结构", "value": "重资产"}）
            score = _structured_similarity(target_soft_tags, peer_soft_tags, dimension_weights)
        else:
            score = 0.0

        peer_copy = dict(peer)
        peer_copy["similarity_score"] = round(score, 4)
        scored.append(peer_copy)

    scored.sort(key=lambda p: p["similarity_score"], reverse=True)
    return scored


def _structured_similarity(
    target: list[SoftTag],
    peer_tags: list[dict],
    weights: dict[str, float],
) -> float:
    """结构化的软标签相似度（Jaccard 加权）"""
    if not target:
        return 0.0

    total_weight = 0.0
    matched_weight = 0.0

    # 将 peer 标签转为 {dimension: value} 映射
    peer_map: dict[str, str] = {}
    for pt in peer_tags:
        dim = pt.get("dimension", "")
        val = pt.get("value", "")
        if dim and val:
            peer_map[dim] = val

    for t in target:
        w = weights.get(t.dimension, 0.05)  # 默认权重 0.05
        total_weight += w
        if t.dimension in peer_map and peer_map[t.dimension] == t.value:
            matched_weight += w

    if total_weight == 0:
        return 0.0
    return matched_weight / total_weight


def _simple_string_similarity(
    target: list[SoftTag],
    peer_tag_strings: list[str],
    weights: dict[str, float],
) -> float:
    """基于字符串匹配的简易相似度"""
    if not target:
        return 0.0

    peer_set = set(peer_tag_strings)
    matches = 0
    total_weight = 0.0

    for t in target:
        w = weights.get(t.dimension, 0.05)
        total_weight += w
        if t.value in peer_set:
            matches += 1

    if total_weight == 0:
        return 0.0
    # 简化：按匹配数量加权
    jaccard = matches / max(len(target), len(peer_set))
    return jaccard


# ────────────────────────────────────────────
# 基准计算
# ────────────────────────────────────────────

def _calc_peer_median(peers: list[dict]) -> dict[str, float]:
    """计算同行各指标的中位数（横向基准）"""
    # 收集所有指标 → 值列表
    indicator_values: dict[str, list[float]] = {}
    for peer in peers:
        financials = peer.get("financials", {})
        for ind, val in financials.items():
            if ind not in indicator_values:
                indicator_values[ind] = []
            indicator_values[ind].append(float(val))

    median_map = {}
    for ind, vals in indicator_values.items():
        if vals:
            median_map[ind] = round(statistics.median(vals), 4)

    return median_map


def _calc_historical_mean(all_peers: list[dict], stock_code: str) -> dict[str, float]:
    """计算该公司自身过去 5 年均值（纵向基准）

    排除异常暴雷年份：如果某年某指标变化超过 3 倍标准差，剔除该年
    """
    # 查找该公司
    company = None
    for p in all_peers:
        if p.get("stock_code") == stock_code:
            company = p
            break

    if not company:
        return {}

    historical = company.get("historical", {})
    if not historical:
        # 如果没有年份分层数据，直接用当前 financials 作为均值
        return company.get("financials", {})

    # historical 格式: {"2021": {"Net_Profit": 100, ...}, "2022": {...}, ...}
    indicator_years: dict[str, list[float]] = {}
    for year, indicators in historical.items():
        for ind, val in indicators.items():
            if ind not in indicator_years:
                indicator_years[ind] = []
            indicator_years[ind].append(float(val))

    mean_map = {}
    for ind, vals in indicator_years.items():
        if len(vals) <= 2:
            # 年份太少，直接取均值
            mean_map[ind] = round(statistics.mean(vals), 4)
        else:
            # 剔除偏离超过 3 倍标准差的年份
            mean_val = statistics.mean(vals)
            stdev = statistics.stdev(vals) if len(vals) > 1 else 0
            if stdev > 0:
                filtered = [v for v in vals if abs(v - mean_val) <= 3 * stdev]
                mean_map[ind] = round(statistics.mean(filtered) if filtered else mean_val, 4)
            else:
                mean_map[ind] = round(mean_val, 4)

    return mean_map
