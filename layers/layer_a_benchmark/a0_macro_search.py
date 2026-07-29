"""A0层：外部宏观研报提取——LLM 联网搜索行业宏观事实

两步流程：
1. 生成搜索词：调用 a0_search_query.yaml → LLM 输出 4 维度搜索关键词
2. SerpAPI 搜索 + 提取事实：调用 a0_macro_facts.yaml → 从搜索结果中提取客观事实

核心约束：
- LLM 只提取事实，不输出主观评级（"强烈看好/强烈卖出"等）
- 每条例事实必须带来源和日期
- 矛盾数据跳过不输出
- 某维度无有效数据则输出空

SerpAPI Key 从环境变量 SERPAPI_KEY 读取。

使用方式：
    facts = run_macro_search(ctx)
    # facts = ["今年上半年白酒行业处于去库存尾声", ...]
"""

import json
import logging
import os

from pipeline.context import PipelineContext

logger = logging.getLogger(__name__)

SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
SERPAPI_URL = "https://serpapi.com/search"


def run_macro_search(ctx: PipelineContext) -> list[str]:
    """A0 层主入口：联网搜索 → 提取 4 维度宏观事实

    依赖 ctx 中的：
    - raw_doc.company_overview（公司业务描述）
    - tags（A1 层的 CompanyTags，包含行业标签）
    - financials（B 层数据，用于提取原材料/下游信息）

    Returns:
        客观事实文本列表（跨 4 维度展平），供 D1 路2 使用
    """
    # 准备上下文变量（调度器保证 raw_doc / tags / financials 已就绪）
    business_desc = ctx.raw_doc.company_overview.business_description or ""
    industry_tags = _format_industry_tags(ctx.tags)
    year = str(ctx.raw_doc.metadata.report_year or ctx.financials.year)

    # 步骤1：生成搜索词
    search_queries = _generate_search_queries(
        business_desc=business_desc,
        industry_tags=industry_tags,
        year=year,
    )

    if not search_queries:
        logger.warning("A0: 未生成任何搜索词")
        return []

    # 步骤2：执行搜索 + 提取事实（无 SerpAPI Key 时 LLM 知识兜底）
    all_search_results = _execute_searches(search_queries)

    # 步骤3：LLM 提取事实
    facts = _extract_facts(
        business_desc=business_desc,
        industry_tags=industry_tags,
        year=year,
        search_results=all_search_results,
    )

    logger.info(f"A0: 提取到 {len(facts)} 条宏观事实")
    return facts


# ────────────────────────────────────────
# 步骤1：生成搜索词
# ────────────────────────────────────────

def _generate_search_queries(
    business_desc: str,
    industry_tags: str,
    year: str,
) -> list[str]:
    """调用 LLM 生成 4 维度搜索关键词"""
    from llm.client import LLMClient

    client = LLMClient()
    try:
        response = client.chat(
            "a0_search_query",
            {
                "business_desc": business_desc,
                "industry_tags": industry_tags,
                "year": year,
            },
        )
    except Exception as e:
        logger.warning(f"A0 生成搜索词失败: {e}")
        return _fallback_search_queries(business_desc, industry_tags)

    raw = response if isinstance(response, str) else str(response)
    data = _parse_json(raw)

    # 展平 4 个维度为搜索词列表
    queries = []
    for dim_key in ["维度1_宏观周期", "维度2_上游原材料", "维度3_下游需求", "维度4_外部突变"]:
        dim_queries = data.get(dim_key, [])
        if isinstance(dim_queries, list):
            queries.extend(dim_queries)

    if not queries:
        return _fallback_search_queries(business_desc, industry_tags)

    logger.info(f"A0 生成 {len(queries)} 条搜索词")
    return queries


def _fallback_search_queries(business_desc: str, industry_tags: str) -> list[str]:
    """搜索词生成的降级方案（不依赖 LLM）"""
    queries = []
    # 从业务描述中提取关键行业词
    industry_kw = industry_tags or business_desc[:50]
    queries.append(f"{industry_kw} 行业政策 今年")
    queries.append(f"{industry_kw} 原材料价格 今年")
    queries.append(f"{industry_kw} 市场需求 销量 今年")
    queries.append(f"{industry_kw} 行业 风险 事件 今年")
    return queries


# ────────────────────────────────────────
# 步骤2：SerpAPI 搜索
# ────────────────────────────────────────

def _execute_searches(queries: list[str]) -> str:
    """批量执行 SerpAPI 搜索，合并结果（无 Key 时走 LLM 知识兜底）"""
    if not SERPAPI_KEY:
        logger.info("SERPAPI_KEY 未设置，将使用 LLM 行业知识生成宏观事实")
        return "（无联网搜索结果，请基于自身行业知识按四个维度输出宏观事实）"

    try:
        import urllib.request
        import urllib.parse
    except ImportError:
        logger.warning("urllib 不可用，使用 LLM 知识兜底")
        return "（无联网搜索结果，请基于自身行业知识按四个维度输出宏观事实）"

    all_snippets = []

    for query in queries[:12]:
        try:
            params = urllib.parse.urlencode({
                "q": query,
                "api_key": SERPAPI_KEY,
                "engine": "google",
                "num": 3,
                "hl": "zh-cn",
            })
            url = f"{SERPAPI_URL}?{params}"

            with urllib.request.urlopen(url, timeout=30) as resp:
                data = json.loads(resp.read().decode())

            organic = data.get("organic_results", [])
            for result in organic:
                title = result.get("title", "")
                snippet = result.get("snippet", "")
                source = result.get("source", result.get("link", ""))
                if snippet:
                    all_snippets.append(f"[{title}] {snippet}\n来源: {source}")

        except Exception as e:
            logger.warning(f"搜索失败 [{query}]: {e}")
            continue

    if not all_snippets:
        return "（无联网搜索结果，请基于自身行业知识按四个维度输出宏观事实）"

    # 合并为文本
    combined = "\n\n---\n\n".join(all_snippets)
    if len(combined) > 8000:
        combined = combined[:8000] + "\n...(搜索结果过长，已截断)"
    return combined


# ────────────────────────────────────────
# 步骤3：提取事实
# ────────────────────────────────────────

def _extract_facts(
    business_desc: str,
    industry_tags: str,
    year: str,
    search_results: str,
) -> list[str]:
    """调用 LLM 从搜索结果中提取 4 维度客观事实"""
    from llm.client import LLMClient

    client = LLMClient()
    try:
        response = client.chat(
            "a0_macro_facts",
            {
                "business_desc": business_desc,
                "industry_tags": industry_tags,
                "year": year,
                "search_results": search_results,
            },
        )
    except Exception as e:
        logger.warning(f"A0 提取事实失败: {e}")
        return []

    raw = response if isinstance(response, str) else str(response)
    data = _parse_json(raw)

    # 展平为事实文本列表
    facts = []
    for dim_key in ["维度1_宏观周期", "维度2_上游原材料", "维度3_下游需求", "维度4_外部突变"]:
        dim_facts = data.get(dim_key, [])
        if isinstance(dim_facts, list):
            for item in dim_facts:
                if isinstance(item, dict):
                    fact_text = item.get("事实", item.get("fact", ""))
                    source = item.get("来源", item.get("source", ""))
                    date = item.get("日期", item.get("date", ""))
                    if fact_text:
                        facts.append(f"[{dim_key[3:]}] {fact_text}（{source}, {date}）")

    return facts


# ────────────────────────────────────────
# 上下文提取工具
# ────────────────────────────────────────

def _format_industry_tags(tags) -> str:
    """格式化行业标签为 prompt 变量"""
    parts = []
    if tags.hard_tags:
        parts.append("硬标签: " + "; ".join(
            f"{h.system}={h.value}" for h in tags.hard_tags
        ))
    if tags.financial_profile and tags.financial_profile.values:
        from schemas.tags import FINANCIAL_DIMENSIONS
        profile_items = [
            f"{FINANCIAL_DIMENSIONS[i]}={tags.financial_profile.values[i]:.2f}"
            for i in range(len(tags.financial_profile.values))
            if i < len(FINANCIAL_DIMENSIONS)
        ]
        parts.append("财务画像: " + " | ".join(profile_items))
    return "\n".join(parts)


# ────────────────────────────────────────
# 工具
# ────────────────────────────────────────

def _parse_json(raw: str) -> dict:
    """安全解析 JSON"""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        import re
        match = re.search(r'\{[\s\S]*\}', raw)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        logger.warning(f"A0 JSON 解析失败: {raw[:200]}")
        return {}
