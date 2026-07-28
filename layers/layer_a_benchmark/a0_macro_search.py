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
from typing import Optional

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
    if not ctx.raw_doc:
        logger.warning("A0: raw_doc 不存在，跳过宏观搜索")
        return []

    # 准备上下文变量
    business_desc = ctx.raw_doc.company_overview.business_description or ""
    industry_tags = _format_industry_tags(ctx)
    year = str(ctx.raw_doc.metadata.report_year or ctx.financials.year if ctx.financials else 2024)
    key_raw_materials = _extract_materials(ctx)
    downstream_markets = _extract_downstream(ctx)

    # 步骤1：生成搜索词
    search_queries = _generate_search_queries(
        business_desc=business_desc,
        industry_tags=industry_tags,
        key_raw_materials=key_raw_materials,
        downstream_markets=downstream_markets,
        year=year,
    )

    if not search_queries:
        logger.warning("A0: 未生成任何搜索词")
        return []

    # 步骤2：执行搜索 + 提取事实
    all_search_results = _execute_searches(search_queries)

    if not all_search_results:
        logger.warning("A0: SerpAPI 搜索无结果")
        return []

    # 步骤3：LLM 提取事实
    facts = _extract_facts(
        business_desc=business_desc,
        industry_tags=industry_tags,
        key_raw_materials=key_raw_materials,
        downstream_markets=downstream_markets,
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
    key_raw_materials: str,
    downstream_markets: str,
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
                "key_raw_materials": key_raw_materials,
                "downstream_markets": downstream_markets,
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
    """批量执行 SerpAPI 搜索，合并结果

    每维度取前 3 条搜索词，每条搜索取前 3 条结果。
    """
    if not SERPAPI_KEY:
        logger.warning("SERPAPI_KEY 未设置，跳过联网搜索。请在 .env 中配置")
        return ""

    try:
        import urllib.request
        import urllib.parse
    except ImportError:
        logger.warning("urllib 不可用，跳过搜索")
        return ""

    all_snippets = []

    for query in queries[:12]:  # 最多 12 条搜索词（4维度 × 3词）
        try:
            params = urllib.parse.urlencode({
                "q": query,
                "api_key": SERPAPI_KEY,
                "engine": "google",
                "num": 3,      # 每条搜索取 3 条结果
                "hl": "zh-cn",  # 中文优先
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
        return ""

    # 合并为文本（token 优化：截断总长）
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
    key_raw_materials: str,
    downstream_markets: str,
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
                "key_raw_materials": key_raw_materials,
                "downstream_markets": downstream_markets,
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

def _format_industry_tags(ctx: PipelineContext) -> str:
    """格式化行业标签为 prompt 变量"""
    tags = ctx.tags
    if not tags:
        return "行业标签未知"

    parts = []
    if tags.hard_tags:
        parts.append("硬标签: " + "; ".join(
            f"{h.system}={h.value}" for h in tags.hard_tags
        ))
    if tags.soft_tags:
        parts.append("软标签: " + "; ".join(
            f"{s.dimension}={s.value}" for s in tags.soft_tags
        ))
    return "\n".join(parts)


def _extract_materials(ctx: PipelineContext) -> str:
    """从 B 层财务数据中尝试推断核心原材料

    策略：查看营业成本（Cost_Revenue）的构成，
    从行业标签推断典型原材料。
    实际上原材料信息更多来自行业知识，这里做基础推断。
    """
    # 从行业标签推断
    if ctx.tags:
        for ht in ctx.tags.hard_tags:
            industry = ht.value
            material_map = {
                "白酒": "高粱、小麦、包装材料",
                "光伏制造设备": "硅料、银浆、光伏玻璃",
                "锂电池": "碳酸锂、钴、镍、石墨",
                "新能源汽车": "电池、芯片、钢材",
                "半导体制造": "硅片、光刻胶、电子特气",
                "化学制药": "原料药、中间体",
                "房地产开发": "土地、建材、钢材",
                "啤酒": "大麦、啤酒花、玻璃瓶",
                "生猪养殖": "玉米、豆粕、饲料",
                "空调": "铜、铝、压缩机",
            }
            if industry in material_map:
                return material_map[industry]

    return "请根据公司业务描述推断"


def _extract_downstream(ctx: PipelineContext) -> str:
    """推断下游领域"""
    # 从公司概要和标签推断
    desc = ctx.raw_doc.company_overview.business_description if ctx.raw_doc else ""
    if ctx.tags:
        customer_type = ""
        for st in ctx.tags.soft_tags:
            if st.dimension == "客户类型":
                customer_type = st.value
        if customer_type == "To-C":
            return f"终端消费者（{desc[:50]}）"
        elif customer_type == "To-B":
            return f"企业客户（{desc[:50]}）"
        elif customer_type == "To-G":
            return "政府及公共事业单位"
    return f"请根据公司业务描述推断（{desc[:80]}）"


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
