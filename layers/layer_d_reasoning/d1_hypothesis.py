"""D1路2：不看原文，基于宏观事实和行业标签推演假设"""

from typing import Any, Optional
from schemas.tags import CompanyTags
from schemas.reasoning import Hypothesis


def generate_hypotheses(
    anomaly: Any,
    macro_facts: list[str],
    tags: Optional[CompanyTags],
    deviation: Optional[Any] = None,
) -> list[Hypothesis]:
    """大模型发散出几种合理的假设

    输入：宏观事实 + 行业标签 + 偏差数据（不看原文）
    流程：组装 prompt → 调用 LLM → 解析假设列表
    输出：最多 4 条 Hypothesis
    """
    # TODO: 调用 LLM 进行商业推演
    return []


def _build_hypothesis_prompt(anomaly, macro_facts, tags, deviation) -> str:
    """组装推演 prompt"""
    parts = [f"异常指标：{getattr(anomaly, 'indicator', getattr(anomaly, 'check_name', ''))}"]
    if deviation:
        parts.append(f"偏离程度：{deviation.mad_multiple:.2f}倍MAD")
    if macro_facts:
        parts.append(f"宏观背景：{'；'.join(macro_facts[:3])}")
    if tags:
        tag_str = "; ".join([f"{t.dimension}={t.value}" for t in tags.soft_tags])
        parts.append(f"行业特征：{tag_str}")
    return "\n".join(parts)
