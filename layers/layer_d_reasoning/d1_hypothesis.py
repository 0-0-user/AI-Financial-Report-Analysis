"""D1路2：外部情报推演——不看原文，基于外部信息发散假设

职责：
- 接收异常清单 + 行业标签 + A0 宏观事实 + 公司基本情况 + LLM 自身常识
- LLM 基于多源信息发散出互斥的合理解释
- 按来源权威性 × 共识强度排序

知识源（仅限以下）：
1. 行业标签（同花顺行业分类）
2. 外部宏观事实（A0 层 4 维度）
3. LLM 自身商业与行业预训练知识
4. 公司基本情况（主营业务描述）

禁止使用：
- 年报原文（MD&A / 附注）
- 财务画像（FinancialProfile）

错误处理：
- LLM 调用失败 → 异常直接向上传播（不做降级）
- 整个 D 层及后续分析中断
"""

import json
import logging
import re
from typing import Any, Optional

from schemas.tags import CompanyTags
from schemas.reasoning import Hypothesis

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────

def generate_hypotheses(
    anomaly: Any,
    macro_facts: list[str],
    tags: Optional[CompanyTags] = None,
    business_desc: str = "",
) -> list[Hypothesis]:
    """路2：基于外部信息 + 行业常识推演假设

    Args:
        anomaly: LogicAnomaly 或 DeviationAnomaly
        macro_facts: A0 层输出的宏观事实列表
        tags: A1 层输出的 CompanyTags（仅使用 hard_tags）
        business_desc: A1 层提取的公司主营业务描述

    Returns:
        按多源交叉排序的 Hypothesis 列表

    Raises:
        Exception: LLM 调用失败时向上传播
    """
    indicator_name = _extract_indicator_name(anomaly)
    actual_value = _extract_actual_value(anomaly)
    deviation_text = _format_deviation(anomaly)
    industry_text = _format_industry_tags(tags)

    macro_text = "\n".join(
        f"{i+1}. {fact}" for i, fact in enumerate(macro_facts)
    ) if macro_facts else "（无宏观事实数据）"

    return _llm_generate(
        indicator=indicator_name,
        actual_value=actual_value,
        deviation=deviation_text,
        business_desc=business_desc,
        industry_tags=industry_text,
        macro_facts=macro_text,
    )


# ────────────────────────────────────────────
# 从异常对象提取信息
# ────────────────────────────────────────────

def _extract_indicator_name(anomaly: Any) -> str:
    """提取异常指标名称"""
    for attr in ["indicator", "check_name"]:
        name = getattr(anomaly, attr, None)
        if name:
            return str(name)
    return "未知指标"


def _extract_actual_value(anomaly: Any) -> str:
    """提取实际值描述"""
    for attr in ["actual_value", "value"]:
        v = getattr(anomaly, attr, None)
        if v is not None:
            return str(v)
    return "未知"


def _format_deviation(anomaly: Any) -> str:
    """格式化偏离度描述"""
    mad = getattr(anomaly, "mad_multiple", None)
    if mad is not None:
        severity = "极端" if mad >= 5.0 else "显著" if mad >= 2.0 else "轻微"
        parts = [f"偏离同行中位数 {mad:.1f} 倍 MAD（{severity}）"]
        actual = getattr(anomaly, "actual_value", None)
        if actual is not None:
            parts.append(f"实际值={actual}")
        return "，".join(parts)

    value = getattr(anomaly, "value", None)
    threshold = getattr(anomaly, "threshold", None)
    if value is not None:
        return f"实际值={value}" + (f"，阈值={threshold}" if threshold else "")
    return "偏离度未知"


def _format_industry_tags(tags: Optional[CompanyTags]) -> str:
    """格式化行业标签"""
    if not tags or not tags.hard_tags:
        return "（无行业标签）"
    hard_str = "；".join(f"{h.system}: {h.value}" for h in tags.hard_tags)
    return hard_str


# ────────────────────────────────────────────
# LLM 调用
# ────────────────────────────────────────────

def _llm_generate(
    indicator: str,
    actual_value: str,
    deviation: str,
    business_desc: str,
    industry_tags: str,
    macro_facts: str,
) -> list[Hypothesis]:
    """调用 LLM 推演假设并按多源交叉排序

    知识源约束（路2 隔离规则）：
    - 仅接收异常清单 + 行业标签 + 宏观事实 + 公司基本情况
    - 不接收年报原文、不接收财务画像
    """
    from llm.client import LLMClient

    client = LLMClient()
    response = client.chat(
        "d1_hypothesis",
        {
            "indicator": indicator,
            "actual_value": actual_value,
            "deviation": deviation,
            "business_desc": business_desc or "（无主营业务描述）",
            "industry_tags": industry_tags,
            "macro_facts": macro_facts,
        },
    )

    raw = response if isinstance(response, str) else str(response)
    return _parse_result(raw)


# ────────────────────────────────────────────
# 结果解析
# ────────────────────────────────────────────

def _parse_result(raw: str) -> list[Hypothesis]:
    """解析 LLM 返回的 JSON，转换为 Hypothesis 列表"""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"LLM 返回非 JSON 格式，尝试提取 JSON 块: {raw[:100]}")
        match = re.search(r'\{[\s\S]*\}', raw)
        if not match:
            logger.error(f"无法解析 LLM 输出: {raw[:200]}")
            return []
        data = json.loads(match.group(0))

    explanations = data.get("explanations", []) if isinstance(data, dict) else data
    if not isinstance(explanations, list):
        return []

    results = []
    for item in explanations:
        if not isinstance(item, dict):
            continue
        hypothesis_text = item.get("explanation", "")
        if not hypothesis_text:
            continue

        rank = int(item.get("confidence_rank", 1))
        sources = item.get("sources", [])

        # 从 sources 构建 reasoning 和 reasoning_source
        reasoning_parts = []
        source_labels = []
        for s in sources:
            stype = s.get("type", "")
            detail = s.get("detail", "")
            name = s.get("name", "")
            source_str = f"{stype}" + (f"：{name}" if name else "") + (f"（{detail}）" if detail else "")
            reasoning_parts.append(source_str)
            source_labels.append(stype)

        reasoning = "；".join(reasoning_parts) if reasoning_parts else ""
        source = "、".join(set(source_labels)) if source_labels else "外部推演"

        results.append(Hypothesis(
            hypothesis=hypothesis_text,
            reasoning=reasoning,
            source=source,
            confidence_rank=rank,
        ))

    # 按 confidence_rank 升序排列（1 排最前）
    results.sort(key=lambda x: x.confidence_rank)
    return results
