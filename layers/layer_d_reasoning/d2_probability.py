"""D2层：概率分配——结合双路径结果给出概率"""

from typing import Optional
from schemas.tags import CompanyTags
from schemas.reasoning import ProbabilityAssignment


def run_probability_allocation(
    reasoning_results: list[dict],
    macro_facts: Optional[list[str]],
    tags: Optional[CompanyTags],
) -> list[ProbabilityAssignment]:
    """对每个异常进行概率分配

    规则：
    - 路1有明确原文解释 + 路2无冲突 → 原文归因高概率
    - 路1无直接解释 → 以路2假设为主
    - "其他"类概率不超过 20%
    - 所有概率加起来 = 100%
    """
    assignments = []

    for result in reasoning_results:
        anomaly = result["anomaly"]
        indicator = getattr(anomaly, "indicator", getattr(anomaly, "check_name", ""))

        # TODO: 调用 LLM 进行概率分配
        probabilities = _default_probabilities(result)

        assignment = ProbabilityAssignment(
            anomaly_indicator=indicator,
            anomaly_source=result["source"],
            lookups=result.get("lookup", []),
            hypotheses=result.get("hypotheses", []),
            probabilities=probabilities,
        )
        assignments.append(assignment)

    return assignments


def _default_probabilities(result: dict) -> dict[str, float]:
    """默认概率分配（当 LLM 不可用时）"""
    lookups = result.get("lookup", [])
    hypotheses = result.get("hypotheses", [])

    if not lookups and not hypotheses:
        return {"其他": 1.0}

    if lookups and any(not e.is_vague for e in lookups):
        return {"年报解释": 0.7, "其他因素": 0.3}

    return {"假设分析": 0.6, "其他": 0.4}
