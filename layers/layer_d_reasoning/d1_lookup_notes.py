"""D1路1：从年报附注和管理层讨论中查找异常的解释

职责：
- 根据异常指标关键词，在年报原文中搜索相关段落
- 调用 LLM 提取关键解释信息
- 标注原文是否含糊其辞

LLM 不可用时降级为关键词匹配 + 直接引用原文。
"""

import json
import logging
from typing import Any

from schemas.raw_doc import RawDocument
from schemas.reasoning import Explanation

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────

def lookup_in_annual_report(raw_doc: RawDocument, anomaly: Any) -> list[Explanation]:
    """大模型读取年报原文，查找与异常指标相关的解释

    Args:
        raw_doc: 第0层输出的 RawDocument
        anomaly: LogicAnomaly 或 DeviationAnomaly

    Returns:
        Explanation 列表（可能为空 = 无直接解释）
    """
    # 提取异常指标名称和关键词
    indicator_name = _extract_indicator_name(anomaly)
    keywords = _indicator_to_keywords(indicator_name)

    # 搜索相关段落
    sections = _find_relevant_sections(raw_doc, keywords)

    if not sections:
        return []

    # 尝试 LLM 提取
    try:
        return _llm_lookup(indicator_name, sections, anomaly)
    except Exception as e:
        logger.warning(f"LLM 查原文失败，降级为规则提取: {e}")
        return _rule_based_lookup(indicator_name, sections)


def _extract_indicator_name(anomaly: Any) -> str:
    """从 anomally 对象中提取指标名"""
    for attr in ["indicator", "check_name"]:
        name = getattr(anomaly, attr, None)
        if name:
            return str(name)
    return str(anomaly)


def _indicator_to_keywords(indicator_name: str) -> list[str]:
    """将指标名映射为搜索关键词"""
    keyword_map = {
        "存货周转率": ["存货", "跌价", "库存", "库龄", "备货"],
        "应收账款周转率": ["应收", "回款", "账龄", "坏账", "信用"],
        "毛利率": ["毛利", "成本", "定价", "原材料", "采购价格"],
        "净利率": ["净利", "费用", "利润", "盈利"],
        "资产负债率": ["负债", "借款", "贷款", "融资", "杠杆"],
        "流动比率": ["流动", "短期偿债", "营运资金"],
        "营业收入增长率": ["收入", "销售", "营收增长", "市场"],
        "净利润增长率": ["净利", "利润增长", "盈利"],
        "净现比": ["现金流", "经营现金流", "回款", "应收应付"],
        "收现比": ["销售回款", "现金收入", "收款"],
        "存贷双高": ["货币资金", "借款", "存款", "贷款", "资金"],
        "母子资金分离度": ["子公司", "母公司", "资金归集", "归集", "财务公司"],
        "ROE": ["净资产收益率", "股东回报", "盈利"],
        "ROA": ["总资产收益率", "资产回报"],
    }
    return keyword_map.get(indicator_name, [indicator_name])


# ────────────────────────────────────────────
# 搜索相关段落
# ────────────────────────────────────────────

def _find_relevant_sections(raw_doc: RawDocument, keywords: list[str]) -> list[dict]:
    """根据关键词在年报原文中定位相关段落

    搜索范围：
    - 附注明细（footnotes.items）
    - 管理层讨论与分析（management_discussion.sections）

    返回：匹配到的段落列表 [{"name": str, "content": str, "page_number": int}, ...]
    """
    sections: list[dict] = []

    for item in raw_doc.footnotes.items[:]:
        item_name = (item.get("name", "") or "").lower()
        item_content = (item.get("content", "") or "").lower()
        combined = item_name + " " + item_content
        if any(kw.lower() in combined for kw in keywords):
            sections.append({
                "type": "附注",
                "name": item.get("name", "附注条目"),
                "content": item.get("content", ""),
                "page_number": item.get("page_number", 0),
            })

    for section in raw_doc.management_discussion.sections[:]:
        title = (section.get("title", "") or "").lower()
        content = (section.get("content", "") or "").lower()
        combined = title + " " + content
        if any(kw.lower() in combined for kw in keywords):
            sections.append({
                "type": "管理层讨论",
                "name": section.get("title", "管理层讨论"),
                "content": section.get("content", ""),
                "page_number": section.get("page_number", 0),
            })

    # 限制数量避免 prompt 过长
    return sections[:8]


# ────────────────────────────────────────────
# LLM 提取
# ────────────────────────────────────────────

def _llm_lookup(
    indicator: str,
    sections: list[dict],
    anomaly: Any,
) -> list[Explanation]:
    """调用 LLM 从相关段落中提取客观解释"""
    from llm.client import LLMClient

    # 构建搜索内容摘要
    relevant_text = _build_section_summary(sections)

    # 获取偏差倍数
    deviation_str = "未知"
    if hasattr(anomaly, "mad_multiple"):
        deviation_str = f"{anomaly.mad_multiple:.1f} 倍 MAD"
    actual_value = getattr(anomaly, "actual_value", "未知")

    client = LLMClient()
    response = client.chat(
        "d1_lookup_notes",
        {
            "indicator": indicator,
            "actual_value": str(actual_value),
            "deviation": deviation_str,
            "relevant_sections": relevant_text,
        },
    )

    return _parse_llm_lookup_result(response if isinstance(response, str) else str(response))


def _build_section_summary(sections: list[dict]) -> str:
    """构建段落摘要文本"""
    lines = []
    for s in sections:
        content_preview = s["content"][:600] if s["content"] else "(无内容)"
        lines.append(
            f"[{s['type']}] {s['name']} (第{s['page_number']}页)\n{content_preview}"
        )
    return "\n\n---\n\n".join(lines) if lines else "(未找到相关段落)"


def _parse_llm_lookup_result(raw: str) -> list[Explanation]:
    """解析 LLM 返回的原文解释结果"""
    try:
        # 尝试 JSON 数组格式
        data = json.loads(raw)
        if not isinstance(data, list):
            data = [data]
        return [
            Explanation(
                summary=d.get("summary", ""),
                source_text=d.get("source_text", d.get("excerpt", "")),
                page_number=d.get("page_number", 0),
                is_vague=d.get("is_vague", d.get("vague", False)),
            )
            for d in data
        ]
    except (json.JSONDecodeError, TypeError, KeyError):
        # 解析失败，返回原始文本作为一条解释
        if raw and raw.strip() and "无直接解释" not in raw:
            return [Explanation(
                summary=f"LLM 原始输出（未解析）",
                source_text=raw[:300],
                page_number=0,
                is_vague=True,
            )]
        return []


# ────────────────────────────────────────────
# 规则降级路径
# ────────────────────────────────────────────

def _rule_based_lookup(indicator: str, sections: list[dict]) -> list[Explanation]:
    """无 LLM 时的关键词匹配降级方案"""
    explanations = []
    for s in sections[:3]:  # 最多取 3 条
        # 简单的模糊判断：如果内容较短且含数字，视为较可信
        content = s.get("content", "")
        is_vague = len(content) < 80  # 内容太短视为含糊

        explanations.append(Explanation(
            summary=f"年报{s['type']}提及 {indicator}",
            source_text=content[:300],
            page_number=s.get("page_number", 0),
            is_vague=is_vague,
        ))

    return explanations
