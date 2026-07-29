"""D1路1：年报原文搬运工——原文说啥搬啥

职责：
- 接收年报原文（MD&A + 附注）+ 异常清单
- LLM 全权翻阅原文，找出与异常指标相关的解释
- 按显要程度排序（篇幅最长、位置最前 = 最可信）
- 不调自身常识、不做推测发散

错误处理：
- LLM 调用失败 → 异常直接向上传播（不做降级）
- 整个 D 层及后续分析中断
"""

import json
import logging
import re
from typing import Any

from schemas.raw_doc import RawDocument
from schemas.reasoning import Explanation

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────

def lookup_in_annual_report(raw_doc: RawDocument, anomaly: Any) -> list[Explanation]:
    """路1：从年报原文中找出异常指标的解释

    纯搬运逻辑：
    - 全量原文传给 LLM，不做关键词预过滤
    - LLM 失败时异常直抛，不做降级

    Args:
        raw_doc: 第 0 层输出的 RawDocument（管理层讨论 + 附注）
        anomaly: LogicAnomaly 或 DeviationAnomaly

    Returns:
        按显要程度排序的 Explanation 列表（可能为空）

    Raises:
        Exception: LLM 调用失败时向上传播
    """
    indicator_name = _extract_indicator_name(anomaly)
    actual_value = _extract_actual_value(anomaly)
    deviation_text = _format_deviation(anomaly)

    # 准备原文全文（不预过滤）
    md_text = _build_md_text(raw_doc.management_discussion)
    footnotes_text = _build_footnotes_text(raw_doc.footnotes)

    return _llm_lookup(indicator_name, actual_value, deviation_text, md_text, footnotes_text)


# ────────────────────────────────────────────
# 从异常对象提取信息
# ────────────────────────────────────────────

def _extract_indicator_name(anomaly: Any) -> str:
    """从 anomaly 对象中提取指标名"""
    for attr in ["indicator", "check_name"]:
        name = getattr(anomaly, attr, None)
        if name:
            return str(name)
    return str(anomaly)


def _extract_actual_value(anomaly: Any) -> str:
    """提取实际值描述"""
    for attr in ["actual_value", "value"]:
        v = getattr(anomaly, attr, None)
        if v is not None:
            return str(v)
    return "未知"


def _format_deviation(anomaly: Any) -> str:
    """格式偏离度描述"""
    mad = getattr(anomaly, "mad_multiple", None)
    if mad is not None:
        severity = "极端" if mad >= 5.0 else "显著" if mad >= 2.0 else "轻微"
        return f"偏离同行中位数 {mad:.1f} 倍 MAD（{severity}）"

    value = getattr(anomaly, "value", None)
    threshold = getattr(anomaly, "threshold", None)
    if value is not None:
        return f"实际值={value}" + (f"，阈值={threshold}" if threshold else "")

    return "偏离度未知"


# ────────────────────────────────────────────
# 原文文本构建
# ────────────────────────────────────────────

def _build_md_text(management_discussion: Any) -> str:
    """将管理层讨论与分析部分拼接为全文文本"""
    sections = getattr(management_discussion, "sections", [])
    if not sections:
        return "（无管理层讨论与分析内容）"

    lines = []
    for sec in sections:
        title = sec.get("title", "") if isinstance(sec, dict) else getattr(sec, "title", "")
        content = sec.get("content", "") if isinstance(sec, dict) else getattr(sec, "content", "")
        page = sec.get("page_number", 0) if isinstance(sec, dict) else getattr(sec, "page_number", 0)
        lines.append(f"=== {title}（第{page}页）===\n{content}")

    return "\n\n".join(lines)


def _build_footnotes_text(footnotes: Any) -> str:
    """将附注明细拼接为全文文本"""
    items = getattr(footnotes, "items", [])
    if not items:
        return "（无附注内容）"

    lines = []
    for item in items:
        name = item.get("name", "") if isinstance(item, dict) else getattr(item, "name", "")
        content = item.get("content", "") if isinstance(item, dict) else getattr(item, "content", "")
        page = item.get("page_number", 0) if isinstance(item, dict) else getattr(item, "page_number", 0)
        is_table = item.get("is_table", False) if isinstance(item, dict) else getattr(item, "is_table", False)
        tag = " [表格]" if is_table else ""
        lines.append(f"【{name}】（第{page}页）{tag}\n{content}")

    return "\n\n".join(lines)


# ────────────────────────────────────────────
# LLM 调用
# ────────────────────────────────────────────

def _llm_lookup(
    indicator: str,
    actual_value: str,
    deviation: str,
    md_text: str,
    footnotes_text: str,
) -> list[Explanation]:
    """调用 LLM 翻阅原文，提取解释并按显要程度排序

    知识源约束（路1 隔离规则）：
    - 仅接收 raw_doc（md_text + footnotes_text）+ 异常清单
    - 不接收任何 A 层数据（macro_facts, tags, financial_profile）
    - LLM 仅做提取，不得用自身知识补充
    """
    from llm.client import LLMClient

    client = LLMClient()
    response = client.chat(
        "d1_lookup_notes",
        {
            "indicator": indicator,
            "actual_value": actual_value,
            "deviation": deviation,
            "md_text": md_text,
            "footnotes_text": footnotes_text,
        },
    )

    raw = response if isinstance(response, str) else str(response)
    return _parse_result(raw)


# ────────────────────────────────────────────
# 结果解析
# ────────────────────────────────────────────

def _parse_result(raw: str) -> list[Explanation]:
    """解析 LLM 返回的 JSON，转换为 Explanation 列表"""
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
        explanation_text = item.get("explanation", "")
        if not explanation_text:
            continue

        rank = int(item.get("confidence_rank", 1))
        sources = item.get("sources", [])

        # 取第一条 source 作为主要来源
        primary_source = sources[0] if sources else {}
        source_type = primary_source.get("type", "年报原文")
        location = primary_source.get("location", "")
        excerpt = primary_source.get("excerpt", "")

        # 从 location 提取页码
        page_number = _extract_page_number(location)

        results.append(Explanation(
            summary=explanation_text,
            source_text=excerpt,
            page_number=page_number,
            is_vague=False,          # 路1 不做模糊判断，原文有就是有
            confidence_rank=rank,
        ))

    # 按 confidence_rank 升序排列（1 排最前）
    results.sort(key=lambda x: x.confidence_rank)
    return results


def _extract_page_number(location: str) -> int:
    """从"第15页"、"15页"或"15"中提取页码"""
    if not location:
        return 0
    match = re.search(r'(\d+)', location)
    return int(match.group(1)) if match else 0
