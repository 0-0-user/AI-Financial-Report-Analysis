"""D2层：概率分配——结合双路径结果给出概率

职责：
- 对 D1 产生的每个异常的 lookups（路1）+ hypotheses（路2）进行综合
- 输出归因及对应概率（所有概率之和 = 1.0）
- "其他"类概率不超过 20%

核心合并逻辑：
1. 路1有明确原文解释 + 路2无冲突 → 原文归因 > 60%
2. 路1无直接解释 → 路2假设为主
3. 路1与路2冲突 → 路1为主（原文比推演更可信）
4. 两路都没结果 → "无法判断"概率高

LLM 不可用时使用规则算法。
"""

import json
import logging
from typing import Optional

from schemas.tags import CompanyTags
from schemas.reasoning import ProbabilityAssignment, Explanation, Hypothesis

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────

def run_probability_allocation(
    reasoning_results: list[dict],
    macro_facts: Optional[list[str]] = None,
    tags: Optional[CompanyTags] = None,
) -> list[ProbabilityAssignment]:
    """对每个异常进行概率分配

    输入每条 reasoning_result 的格式：
    {
        "source": "B+" | "C",
        "anomaly": LogicAnomaly | DeviationAnomaly,
        "lookup": [Explanation, ...],      # D1 路1结果
        "hypotheses": [Hypothesis, ...],   # D1 路2结果
    }

    Args:
        reasoning_results: D1 双路径结果列表
        macro_facts: 宏观事实（可选，用于 LLM prompt 上下文）
        tags: 行业标签（可选）

    Returns:
        ProbabilityAssignment 列表
    """
    assignments: list[ProbabilityAssignment] = []

    for result in reasoning_results:
        anomaly = result["anomaly"]
        indicator = _extract_indicator(anomaly)
        lookup_results = result.get("lookup", [])
        hypothesis_results = result.get("hypotheses", [])

        # 尝试 LLM 概率分配
        try:
            probabilities = _llm_probability_allocation(
                indicator=indicator,
                source=result["source"],
                lookups=lookup_results,
                hypotheses=hypothesis_results,
            )
        except Exception as e:
            logger.warning(f"LLM 概率分配失败，降级为规则算法: {e}")
            probabilities = _rule_based_probability(lookup_results, hypothesis_results)

        # 确保概率和为 1.0
        probabilities = _normalize_probabilities(probabilities)

        assignment = ProbabilityAssignment(
            anomaly_indicator=indicator,
            anomaly_source=result["source"],
            lookups=lookup_results,
            hypotheses=hypothesis_results,
            probabilities=probabilities,
        )
        assignments.append(assignment)

    return assignments


def _extract_indicator(anomaly) -> str:
    """提取异常指标名"""
    for attr in ["indicator", "check_name"]:
        name = getattr(anomaly, attr, None)
        if name:
            return str(name)
    return "未知指标"


# ────────────────────────────────────────────
# LLM 路径
# ────────────────────────────────────────────

def _llm_probability_allocation(
    indicator: str,
    source: str,
    lookups: list[Explanation],
    hypotheses: list[Hypothesis],
) -> dict[str, float]:
    """调用 LLM 进行概率分配"""
    from llm.client import LLMClient

    lookup_text = _format_lookups(lookups)
    hypothesis_text = _format_hypotheses(hypotheses)

    client = LLMClient()
    response = client.chat(
        "d2_probability",
        {
            "indicator": indicator,
            "source": source,
            "lookup_results": lookup_text,
            "hypothesis_results": hypothesis_text,
        },
    )

    raw = response if isinstance(response, str) else str(response)
    data = _parse_llm_probability_result(raw)
    return data.get("probabilities", {"无法判断": 1.0})


def _format_lookups(lookups: list[Explanation]) -> str:
    """格式化路1结果"""
    if not lookups:
        return "路1未找到直接解释。"
    lines = []
    for i, e in enumerate(lookups, 1):
        vague_tag = "【含糊】" if e.is_vague else ""
        lines.append(f"{i}. {vague_tag}{e.summary}（第{e.page_number}页）")
    return "\n".join(lines)


def _format_hypotheses(hypotheses: list[Hypothesis]) -> str:
    """格式化路2结果"""
    if not hypotheses:
        return "路2未生成推演假设。"
    lines = []
    for i, h in enumerate(hypotheses, 1):
        lines.append(f"{i}. [{h.source}] {h.hypothesis}\n   推演：{h.reasoning}")
    return "\n".join(lines)


def _parse_llm_probability_result(raw: str) -> dict:
    """解析 LLM 返回的概率分配"""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # 尝试提取 {...} 内容
        import re
        match = re.search(r'\{[\s\S]*\}', raw)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        logger.warning(f"无法解析 LLM 概率输出: {raw[:200]}")
        return {"无法判断": 1.0}


# ────────────────────────────────────────────
# 规则降级路径
# ────────────────────────────────────────────

def _rule_based_probability(
    lookups: list[Explanation],
    hypotheses: list[Hypothesis],
) -> dict[str, float]:
    """基于规则的智能概率分配（无 LLM 降级方案）

    合并逻辑：
    1. 路1有明确解释 → 高概率归因
    2. 路1无解释 → 路2假设为主
    3. 两路都有 → 路1权重 70%，路2权重 30%
    """
    # 场景1：两路都空
    if not lookups and not hypotheses:
        return {"信息不足，无法归因": 1.0}

    # 场景2：只有路1有结果
    has_clear_lookup = any(not e.is_vague for e in lookups)

    if has_clear_lookup and not hypotheses:
        # 路1有明确解释，直接归因
        clear_count = sum(1 for e in lookups if not e.is_vague)
        vague_count = len(lookups) - clear_count
        probs = {}
        if clear_count == 1:
            clear_expl = [e for e in lookups if not e.is_vague][0]
            probs[clear_expl.summary[:20]] = 0.70
        else:
            # 多个明确解释，均分
            for e in lookups:
                if not e.is_vague:
                    probs[e.summary[:20]] = 0.70 / clear_count
                else:
                    probs[f"（含糊）{e.summary[:15]}"] = 0.10
        probs["其他因素"] = 1.0 - sum(probs.values())
        return probs

    # 场景3：只有路2有结果
    if not has_clear_lookup and hypotheses:
        probs = {}
        # 分配：第一个假设权重最大
        for i, h in enumerate(hypotheses[:3]):
            probs[h.hypothesis[:20]] = 0.60 / (i + 1) if i < 2 else 0.15
        remaining = 1.0 - sum(probs.values())
        if remaining > 0:
            probs["其他"] = min(remaining, 0.20)
        return probs

    # 场景4：两路都有 → 路1为主（70%），路2为辅（30%）
    probs = {}
    clear_lookups = [e for e in lookups if not e.is_vague]

    if clear_lookups:
        # 路1 占 70%
        per_lookup = 0.70 / len(clear_lookups)
        for e in clear_lookups[:3]:
            probs[e.summary[:20]] = per_lookup
        # 路2 补充 20%
        if hypotheses:
            probs[hypotheses[0].hypothesis[:20]] = 0.20
        else:
            probs["其他因素"] = 0.30
    else:
        # 路1含糊，路2为主
        for i, h in enumerate(hypotheses[:3]):
            probs[h.hypothesis[:20]] = 0.80 / (2 ** i)
        probs["其他"] = 0.10

    return probs


# ────────────────────────────────────────────
# 工具函数
# ────────────────────────────────────────────

def _normalize_probabilities(probs: dict[str, float]) -> dict[str, float]:
    """确保所有概率之和为 1.0

    - 如果总和为 0，均分
    - "其他"类概率上限 20%
    - 四舍五入到 2 位小数
    """
    total = sum(probs.values())
    if total == 0:
        return {"无法判断": 1.0}

    # 归一化
    normalized = {k: v / total for k, v in probs.items()}

    # "其他"类上限 20%
    other_keys = [k for k in normalized if "其他" in k or "其他因素" in k]
    for k in other_keys:
        if normalized[k] > 0.20:
            excess = normalized[k] - 0.20
            normalized[k] = 0.20
            # 超出部分按比例分配给其他项
            non_other = [nk for nk in normalized if nk not in other_keys]
            if non_other and excess > 0:
                per_item = excess / len(non_other)
                for nk in non_other:
                    normalized[nk] += per_item

    # 再次归一化 + 四舍五入
    total2 = sum(normalized.values())
    normalized = {k: round(v / total2, 4) for k, v in normalized.items()}

    # 修正最后一位的舍入误差
    diff = 1.0 - sum(normalized.values())
    if diff != 0 and normalized:
        max_key = max(normalized, key=normalized.get)
        normalized[max_key] = round(normalized[max_key] + diff, 4)

    return normalized


# ────────────────────────────────────────────
# 默认概率（外部兼容）
# ────────────────────────────────────────────

def _default_probabilities(result: dict) -> dict[str, float]:
    """默认概率分配（当 LLM 不可用时的最简方案）"""
    lookups = result.get("lookup", [])
    hypotheses = result.get("hypotheses", [])

    if not lookups and not hypotheses:
        return {"其他": 1.0}

    if lookups and any(not e.is_vague for e in lookups):
        return {"年报解释": 0.7, "其他因素": 0.3}

    if hypotheses:
        return {hypotheses[0].hypothesis[:20]: 0.6, "其他": 0.4}

    return {"假设分析": 0.6, "其他": 0.4}
