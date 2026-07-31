"""D2层: D-S 证据理论概率分配

职责: 
- 对 D1 每个异常的 lookups (路1) + hypotheses (路2) 进行 D-S 证据理论合成
- 输出归因概率 (所有概率之和 = 1.0) 
- 保留 D-S 元数据供分析

核心流程 (纯数学 + 一次轻量 LLM 调用) : 
  Step 0 — LLM 语义合并 + 冲突检测
  Step 1 — 构建路1 mass m₁ (ε₁=0.35) 
  Step 2 — 构建路2 mass m₂ (ε₂=0.30) 
  Step 3 — Dempster 合成
  Step 4 — Pignistic 转概率

原则: 
- LLM 只做语义理解 (合并+冲突判断) ，不做数学运算
- mass 分配和合成全是代码计算
- LLM 失败时不做合并，各自独立运行
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

import yaml

from schemas.tags import CompanyTags
from schemas.reasoning import ProbabilityAssignment

logger = logging.getLogger(__name__)

# 参数配置文件路径
_THRESHOLDS_PATH = Path("config/thresholds.yaml")


# ────────────────────────────────────────────
# 参数加载 (从 config/thresholds.yaml 读取) 
# ────────────────────────────────────────────

def _get_d2_config() -> dict:
    """从 config/thresholds.yaml 加载 D2 层参数，带缓存"""
    if not hasattr(_get_d2_config, "_cache"):
        defaults = {
            "epsilon_1": 0.35,
            "epsilon_2": 0.30,
            "path1_split_primary": 0.70,
            "path1_split_secondary": 0.30,
            "path2_top_n": 5,
            "conflict_threshold": 0.60,
        }
        if _THRESHOLDS_PATH.exists():
            try:
                with open(_THRESHOLDS_PATH, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                d2_cfg = data.get("d2_layer", {})
                defaults.update(d2_cfg)
            except Exception as e:
                logger.warning(f"读取 D2 配置失败，使用默认值: {e}")
        _get_d2_config._cache = defaults
    return _get_d2_config._cache


# ────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────

def run_probability_allocation(
    reasoning_results: list[dict],
    macro_facts: Optional[list[str]] = None,
    tags: Optional[CompanyTags] = None,
) -> list[ProbabilityAssignment]:
    """对每个异常进行 D-S 证据理论概率分配

    输入每条 reasoning_result 的格式: 
    {
        "source": "B+" | "C",
        "anomaly": LogicAnomaly | DeviationAnomaly,
        "lookup": [Explanation, ...],      # D1 路1结果
        "hypotheses": [Hypothesis, ...],   # D1 路2结果
    }

    Args:
        reasoning_results: D1 双路径结果列表
        macro_facts: 宏观事实 (保留兼容，新 D2 不使用) 
        tags: 行业标签 (保留兼容，新 D2 不使用) 

    Returns:
        ProbabilityAssignment 列表 (含 ds_metadata) 
    """
    assignments: list[ProbabilityAssignment] = []

    for result in reasoning_results:
        anomaly = result["anomaly"]
        indicator = _extract_indicator(anomaly)
        lookups = result.get("lookup", [])
        hypotheses = result.get("hypotheses", [])

        # ── Step 0: LLM 语义合并 + 冲突检测 ──
        merged_causes = _llm_merge_conflict(indicator, lookups, hypotheses)

        # ── Step 1: 构建 m₁ (路1 mass)  ──
        path1_causes = [m for m in merged_causes if m["path1_indices"]]
        cfg = _get_d2_config()
        m1 = _build_m1(path1_causes, epsilon_1=cfg["epsilon_1"])

        # ── Step 2: 构建 m₂ (路2 mass)  ──
        path2_causes = [m for m in merged_causes if m["path2_indices"]]
        m2 = _build_m2(path2_causes, hypotheses, epsilon_2=cfg["epsilon_2"], top_n=cfg["path2_top_n"])

        # ── Step 3: Dempster 合成 ──
        conflict_map = _build_conflict_map(merged_causes)
        mass_final, K = _dempster_combine(m1, m2, conflict_map)

        # ── Step 4: Pignistic 转概率 ──
        probs = _pignistic_transform(mass_final)

        # ── 构建 D-S 元数据 ──
        ds_metadata = _build_ds_metadata(
            m1=m1, m2=m2, mass_final=mass_final,
            K=K, merged_causes=merged_causes,
            conflict_threshold=cfg["conflict_threshold"],
        )

        assignment = ProbabilityAssignment(
            anomaly_indicator=indicator,
            anomaly_source=result["source"],
            lookups=lookups,
            hypotheses=hypotheses,
            probabilities=probs,
            ds_metadata=ds_metadata,
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


# ════════════════════════════════════════════
# Step 0: LLM 语义合并 + 冲突检测
# ════════════════════════════════════════════

def _llm_merge_conflict(
    indicator: str,
    lookups: list,
    hypotheses: list,
) -> list[dict]:
    """调用 LLM 进行语义合并和冲突检测

    输入: 路1解释列表 + 路2假设列表
    输出: merged_causes 列表，每条含: 
        - name: 统一归因名称
        - path1_indices: 对应 lookups 的下标列表
        - path2_indices: 对应 hypotheses 的下标列表
        - conflicts_with: 与之冲突的归因名称列表

    LLM 失败时返回不合并的默认结构 (各自独立，无冲突) 。
    """
    if not lookups and not hypotheses:
        return []

    # 构建输入
    lookup_items = []
    for i, e in enumerate(lookups):
        lookup_items.append({
            "index": i,
            "explanation": e.summary,
            "source_text": e.source_text[:200],
        })

    hypothesis_items = []
    for i, h in enumerate(hypotheses):
        hypothesis_items.append({
            "index": i,
            "hypothesis": h.hypothesis,
            "reasoning": h.reasoning[:200],
            "source": h.source,
        })

    try:
        from llm.client import LLMClient
        client = LLMClient()
        response = client.chat(
            "d2_merge_conflict",
            {
                "indicator": indicator,
                "lookups": json.dumps(lookup_items, ensure_ascii=False),
                "hypotheses": json.dumps(hypothesis_items, ensure_ascii=False),
            },
        )
        raw = response if isinstance(response, str) else str(response)
        merged = _parse_merge_result(raw)
        if not merged:
            raise RuntimeError(f"D2 LLM 语义合并失败: 返回空结果, raw={raw[:200]}")
        return merged
    except Exception as e:
        logger.error(f"D2 语义合并失败，终止管道: {e}")
        raise


def _parse_merge_result(raw: str) -> list[dict] | None:
    """解析 LLM 返回的合并结果"""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r'\{[\s\S]*\}', raw)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    if not isinstance(data, dict):
        return None

    merged_causes = data.get("merged_causes", [])
    if not isinstance(merged_causes, list):
        return None

    # 验证每个合并项
    valid = []
    for item in merged_causes:
        if not isinstance(item, dict):
            continue
        name = item.get("name", "").strip()
        if not name:
            continue
        valid.append({
            "name": name,
            "path1_indices": item.get("path1_indices", []),
            "path2_indices": item.get("path2_indices", []),
            "conflicts_with": item.get("conflicts_with", []),
        })

    return valid if valid else None

# ════════════════════════════════════════════
# Step 1: 构建 m₁ (路1 mass) 
# ════════════════════════════════════════════

def _build_m1(
    path1_causes: list[dict],
    epsilon_1: float = 0.35,
    split_primary: float = 0.70,
) -> dict[str, float]:
    """构建路1 mass 函数

    k=0 (空) : m₁(Θ) = 1.0
    k=1:       m₁({E}) = 1-ε₁,  m₁(Θ) = ε₁
    k=2:       m₁({E₁}) = (1-ε₁)xsplit_primary,  m₁({E₂}) = (1-ε₁)x(1-split_primary),  m₁(Θ) = ε₁
    k>=3:       (1-ε₁) 按指数衰减分配
    """
    m1: dict[str, float] = {}
    k = len(path1_causes)

    if k == 0:
        # 空结果: 全部 uncertainty
        return {"Θ": 1.0}

    pool = 1.0 - epsilon_1

    if k == 1:
        m1[path1_causes[0]["name"]] = pool
    elif k == 2:
        m1[path1_causes[0]["name"]] = pool * split_primary
        m1[path1_causes[1]["name"]] = pool * (1.0 - split_primary)
    else:
        # k>=3: 指数衰减，依次占 pool 的 1/2, 1/4, 1/8...
        remaining = pool
        for i, cause in enumerate(path1_causes):
            if i == k - 1:
                # 最后一项拿走所有剩余
                m1[cause["name"]] = remaining
            else:
                share = remaining * 0.5
                m1[cause["name"]] = share
                remaining -= share

    m1["Θ"] = epsilon_1
    return m1


# ════════════════════════════════════════════
# Step 2: 构建 m₂ (路2 mass) 
# ════════════════════════════════════════════

def _build_m2(
    path2_causes: list[dict],
    original_hypotheses: list,
    epsilon_2: float = 0.30,
    top_n: int = 5,
) -> dict[str, float]:
    """构建路2 mass 函数

    - 空: m₂(Θ) = 1.0
    - 有结果: 取 consensus_weight 前 top_n 名
      consensus_weight = source_authority x source_count_bonus
      归一化后 m({H_j}) = norm_cw x (1-ε₂)
    """
    m2: dict[str, float] = {}
    k = len(path2_causes)

    if k == 0:
        return {"Θ": 1.0}

    # 计算每个合并归因的 consensus_weight
    weighted = []
    for cause in path2_causes:
        cw = _calc_consensus_weight(cause, original_hypotheses)
        weighted.append((cause["name"], cw))

    # 保留前 N 名
    weighted.sort(key=lambda x: x[1], reverse=True)
    top = weighted[:top_n]

    # 归一化
    total_cw = sum(w for _, w in top)
    if total_cw <= 0:
        return {"Θ": 1.0}

    pool = 1.0 - epsilon_2
    for name, cw in top:
        m2[name] = (cw / total_cw) * pool

    m2["Θ"] = epsilon_2
    return m2


def _calc_consensus_weight(
    cause: dict,
    original_hypotheses: list,
) -> float:
    """计算合并归因的 consensus_weight

    consensus_weight = source_authority x source_count_bonus

    source_authority 取该归因涉及的所有路2假设中最高权威性。
    source_count_bonus 基于独立来源数量。
    """
    indices = cause.get("path2_indices", [])
    if not indices:
        return 0.1  # 无路2来源的默认值

    # 收集所有涉及的路2假设的 source 字段
    sources_seen = set()
    max_authority = 0.0

    for idx in indices:
        if idx >= len(original_hypotheses):
            continue
        h = original_hypotheses[idx]
        source_str = getattr(h, "source", "") or ""

        # 拆分为独立来源标签 ("、" 分隔) 
        parts = re.split(r"[、,，/]", source_str)
        for part in parts:
            part = part.strip()
            if part:
                sources_seen.add(part)
                auth = _calc_source_authority(part)
                max_authority = max(max_authority, auth)

    count_bonus = _calc_source_count_bonus(len(sources_seen))
    return max_authority * count_bonus


def _calc_source_authority(source_label: str) -> float:
    """根据来源标签计算权威性权重"""
    mapping = {
        "券商深度报告": 1.0,
        "官方公告": 1.0,
        "行业新闻": 0.7,
        "财经媒体": 0.7,
        "A0宏观事实": 0.6,
        "行业常识": 0.5,
        "雪球": 0.3,
        "股吧": 0.3,
        "自媒体": 0.3,
    }
    for key, value in mapping.items():
        if key in source_label:
            return value
    return 0.1  # 纯 LLM 常识


def _calc_source_count_bonus(n_sources: int) -> float:
    """根据独立来源数量计算交叉验证系数"""
    if n_sources >= 3:
        return 1.0
    elif n_sources == 2:
        return 0.7
    elif n_sources == 1:
        return 0.4
    return 0.2


# ════════════════════════════════════════════
# Step 3: Dempster 合成
# ════════════════════════════════════════════

def _build_conflict_map(merged_causes: list[dict]) -> dict[str, set[str]]:
    """构建冲突映射表 {cause_name: {conflicting_cause_names}}"""
    conflict_map: dict[str, set[str]] = {}
    for cause in merged_causes:
        name = cause["name"]
        if name not in conflict_map:
            conflict_map[name] = set()
        for other in cause.get("conflicts_with", []):
            if other and other != name:
                conflict_map[name].add(other)
                # 确保双向映射
                if other not in conflict_map:
                    conflict_map[other] = set()
                conflict_map[other].add(name)
    return conflict_map


def _dempster_combine(
    m1: dict[str, float],
    m2: dict[str, float],
    conflict_map: dict[str, set[str]],
) -> tuple[dict[str, float], float]:
    """Dempster 合成两个 evidence 源的 mass 函数

    只有在 conflict_map 中标记为冲突的归因对才会产生 K。
    非冲突的不同归因之间的交叉项归入 Θ (不确定性) 。
    Θ 作为全集，与其他任何子集的交为该子集本身。

    Args:
        m1: 路1 mass 函数 {cause_name: mass}
        m2: 路2 mass 函数 {cause_name: mass}
        conflict_map: 冲突映射 {cause: {conflicting_causes}}

    Returns:
        (mass_final, K) — 合成后的 mass 和冲突系数
    """
    cause_names = set(m1.keys()) | set(m2.keys())
    cause_names.discard("Θ")

    m_total: dict[str, float] = {name: 0.0 for name in cause_names}
    m_total["Θ"] = 0.0
    K = 0.0

    for elem1, mass1 in m1.items():
        for elem2, mass2 in m2.items():
            product = mass1 * mass2
            if product <= 0:
                continue

            # —— 双方一致指向同一归因 ——
            if elem1 == elem2 and elem1 != "Θ":
                m_total[elem1] += product

            # —— 一方是 Θ (全集) ，另一方保留原样 ——
            elif elem1 == "Θ" and elem2 != "Θ":
                m_total[elem2] += product
            elif elem2 == "Θ" and elem1 != "Θ":
                m_total[elem1] += product

            # —— 双方都是 Θ ——
            elif elem1 == "Θ" and elem2 == "Θ":
                m_total["Θ"] += product

            # —— 双方明确冲突 ——
            elif _is_explicit_conflict(elem1, elem2, conflict_map):
                K += product

            # —— 非冲突的不同归因 -> 归入 uncertainty ——
            else:
                m_total["Θ"] += product

    # 归一化 (去除冲突 K) 
    norm = 1.0 - K
    if norm <= 1e-10:
        # 完全冲突
        return {"Θ": 1.0}, 1.0

    # 处理浮点误差
    m_final = {k: v / norm for k, v in m_total.items()}
    total = sum(m_final.values())
    if total > 0 and abs(total - 1.0) > 1e-6:
        m_final = {k: v / total for k, v in m_final.items()}

    return m_final, K


def _is_explicit_conflict(
    elem1: str,
    elem2: str,
    conflict_map: dict[str, set[str]],
) -> bool:
    """判断两个元素是否在冲突映射表中互斥"""
    if elem1 == "Θ" or elem2 == "Θ":
        return False
    return elem2 in conflict_map.get(elem1, set())


# ════════════════════════════════════════════
# Step 4: Pignistic 转概率
# ════════════════════════════════════════════

def _pignistic_transform(mass_final: dict[str, float]) -> dict[str, float]:
    """将合成后的 mass 转为概率分布

    v5.2 决策: 未知质量(Θ)不摊回已知归因，而是单独分配给"其他原因"桶，
    避免把不确定性伪装成确定性归因。已知归因取各自 mass，Σ = 1.0。
    例: {归因A:0.44, 归因B:0.30, Θ:0.26} → 归因A 44%、归因B 30%、其他 26%。
    """
    probs: dict[str, float] = {}
    for name, mass in mass_final.items():
        if name == "Θ":
            probs["其他原因"] = round(mass, 4)
        else:
            probs[name] = round(mass, 4)

    # 修正最后一位浮点误差
    total = sum(probs.values())
    if total > 0 and abs(total - 1.0) > 1e-4:
        max_key = max(probs, key=probs.get)
        probs[max_key] = round(probs[max_key] + (1.0 - total), 4)

    return probs


# ════════════════════════════════════════════
# D-S 元数据构建
# ════════════════════════════════════════════

def _build_ds_metadata(
    m1: dict[str, float],
    m2: dict[str, float],
    mass_final: dict[str, float],
    K: float,
    merged_causes: list[dict],
    conflict_threshold: float = 0.60,
) -> dict:
    """构建 D-S 元数据 (用于调试和分析) """
    merged_summary = []
    for cause in merged_causes:
        merged_summary.append({
            "name": cause["name"],
            "from_path1": bool(cause["path1_indices"]),
            "from_path2": bool(cause["path2_indices"]),
            "conflicts_with": cause.get("conflicts_with", []),
        })

    return {
        "m1": {k: round(v, 4) for k, v in m1.items()},
        "m2": {k: round(v, 4) for k, v in m2.items()},
        "mass_final": {k: round(v, 4) for k, v in mass_final.items()},
        "conflict_K": round(K, 4),
        "severe_conflict": K > conflict_threshold,
        "merged_causes": merged_summary,
        "merge_failed": not merged_causes,  # 如果空列表则合并失败
    }
