"""D3层: 语义关键词硬性匹配——将D层原因描述映射为语义影响分

职责: 
- 加载 config/semantic_scoring.yaml 语义评分库
- 对 ProbabilityAssignment 中的每条原因描述，调用 LLM 做关键词匹配
- 匹配结果写入 semantic_scores 和 semantic_keywords 字段

匹配策略: 
- LLM 左手接收完整原因描述 + 概率，右手仅限语义库中的关键词
- 取最严重匹配原则: 同时匹配多个级别时取 score_base 最低的
- 失败即终止: LLM 调用或匹配失败直接抛异常
"""

import json
import logging
from pathlib import Path

from schemas.reasoning import ProbabilityAssignment

logger = logging.getLogger(__name__)

_SEMANTIC_PATH = Path("config/semantic_scoring.yaml")


def _load_semantic_levels() -> list[dict]:
    """加载语义评分库"""
    import yaml
    if not _SEMANTIC_PATH.exists():
        raise FileNotFoundError(f"语义评分库不存在: {_SEMANTIC_PATH}")
    with open(_SEMANTIC_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    levels = cfg.get("levels", [])
    if not levels:
        raise RuntimeError("语义评分库为空")
    return levels


def run_semantic_matching(
    probability_assignments: list[ProbabilityAssignment],
) -> list[ProbabilityAssignment]:
    """对每个异常指标的每条归因原因，执行 LLM 语义关键词匹配

    Args:
        probability_assignments: D2 层输出的概率分配列表

    Returns:
        补充了 semantic_scores 和 semantic_keywords 的同一列表
    """
    if not probability_assignments:
        return probability_assignments

    levels = _load_semantic_levels()

    for pa in probability_assignments:
        if not pa.probabilities:
            continue

        scores: dict[str, int] = {}
        keywords: dict[str, list[str]] = {}

        for cause, prob in pa.probabilities.items():
            matched_level, matched_kws = _match_single_cause(
                cause=cause, probability=prob, levels=levels,
            )
            scores[cause] = matched_level
            keywords[cause] = matched_kws

        pa.semantic_scores = scores
        pa.semantic_keywords = keywords

    return probability_assignments


def _match_single_cause(
    cause: str,
    probability: float,
    levels: list[dict],
) -> tuple[int, list[str]]:
    """调用 LLM 对单条原因做语义关键词匹配

    Returns:
        (matched_level, matched_keywords)
    """
    from llm.client import LLMClient

    client = LLMClient()

    prompt_vars = {
        "cause": cause,
        "probability": probability,
        "semantic_levels": levels,
    }

    try:
        response = client.chat("d3_semantic_match", prompt_vars)
    except Exception as e:
        logger.error(f"D3 LLM 语义匹配失败: cause='{cause[:50]}...', error={e}")
        raise

    raw = response if isinstance(response, str) else str(response)
    result = _parse_result(raw)

    if result is None:
        raise RuntimeError(
            f"D3 LLM 语义匹配返回无效结果: cause='{cause[:50]}...', raw={raw[:200]}"
        )

    matched_level = result["matched_level"]
    matched_keywords = result.get("matched_keywords", [])

    # 验证 matched_level 是否在合法范围内
    if matched_level not in (-3, -2, -1, 0, 1, 2):
        logger.warning(
            f"D3 匹配结果超出范围: matched_level={matched_level}，将修正为 -1"
        )
        matched_level = -1

    logger.debug(
        f"D3 matched: level={matched_level}, keywords={matched_keywords}, "
        f"cause='{cause[:40]}...'"
    )
    return matched_level, matched_keywords


def _parse_result(raw: str) -> dict | None:
    """解析 LLM 返回的 JSON"""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        import re
        match = re.search(r'\{[\s\S]*\}', raw)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    if not isinstance(data, dict):
        return None

    matched_level = data.get("matched_level")
    if matched_level is None or not isinstance(matched_level, int):
        return None

    # 验证必要字段
    matched_keywords = data.get("matched_keywords", [])
    if not isinstance(matched_keywords, list):
        matched_keywords = []

    return {
        "matched_level": matched_level,
        "matched_keywords": matched_keywords[:3],  # 最多3个
    }
