"""D1路2：不看原文，基于宏观事实和行业标签推演假设

职责：
- 接收异常指标 + 宏观事实 + 行业标签 + 偏离数据
- 调用 LLM 发散出互斥的合理解释假设
- 每条假设标注推演依据来源

LLM 不可用时降级为模板化假设生成。
"""

import json
import logging
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
    deviation: Optional[Any] = None,
) -> list[Hypothesis]:
    """大模型基于宏观背景和行业常识，发散出合理的假设解释异常

    Args:
        anomaly: LogicAnomaly 或 DeviationAnomaly
        macro_facts: A0 层输出的宏观事实列表
        tags: A1 层输出的 CompanyTags
        deviation: C 层偏差对象（可选，有 mad_multiple 等属性）

    Returns:
        Hypothesis 列表，最多 4 条，每条包含假设内容、推演依据、来源
    """
    # 构建 prompt 上下文
    context = _build_hypothesis_context(anomaly, macro_facts, tags, deviation)

    # 尝试 LLM 生成
    try:
        return _llm_generate_hypotheses(context)
    except Exception as e:
        logger.warning(f"LLM 推演假设失败，降级为模板生成: {e}")
        return _template_hypotheses(anomaly, macro_facts, tags, deviation)


# ────────────────────────────────────────────
# Prompt 上下文构建
# ────────────────────────────────────────────

def _build_hypothesis_context(
    anomaly: Any,
    macro_facts: list[str],
    tags: Optional[CompanyTags],
    deviation: Optional[Any],
) -> dict:
    """组装推演 prompt 所需的完整上下文

    返回结构：
    {
        "indicator": "存货周转率",
        "deviation": "偏离同行中位数 3.2 倍 MAD",
        "macro_facts": "1. 硅料价格下跌20%...",
        "industry_tags": "白酒; 重资产; 高毛利; To-C端"
    }
    """
    indicator = _extract_name(anomaly)
    deviation_text = _format_deviation(deviation or anomaly)

    macro_text = "\n".join(
        f"{i+1}. {fact}" for i, fact in enumerate(macro_facts[:5])
    ) if macro_facts else "（无宏观事实数据）"

    tag_text = "（无行业标签）"
    if tags and tags.soft_tags:
        pairs = [f"{t.dimension}={t.value}" for t in tags.soft_tags]
        tag_text = "；".join(pairs)
    if tags and tags.hard_tags:
        hard_str = "；".join(f"{h.system}: {h.value}" for h in tags.hard_tags)
        tag_text = f"[行业] {hard_str}\n[特征] {tag_text}"

    return {
        "indicator": indicator,
        "deviation": deviation_text,
        "macro_facts": macro_text,
        "industry_tags": tag_text,
    }


def _extract_name(anomaly: Any) -> str:
    """提取异常名称"""
    for attr in ["indicator", "check_name"]:
        name = getattr(anomaly, attr, None)
        if name:
            return str(name)
    return "未知指标"


def _format_deviation(obj: Any) -> str:
    """格式化偏离度描述"""
    if obj is None:
        return "偏离度未知"
    mad = getattr(obj, "mad_multiple", None)
    actual = getattr(obj, "actual_value", None)
    benchmark = getattr(obj, "benchmark_value", None)

    if mad is not None:
        severity = "极端" if mad >= 5.0 else "显著" if mad >= 2.0 else "轻微"
        parts = [f"偏离同行中位数 {mad:.1f} 倍 MAD（{severity}）"]
        if actual is not None:
            parts.append(f"实际值={actual}")
        return "，".join(parts)

    value = getattr(obj, "value", None)
    threshold = getattr(obj, "threshold", None)
    if value is not None:
        return f"实际值={value}" + (f"，阈值={threshold}" if threshold else "")
    return "偏离度未知"


# ────────────────────────────────────────────
# LLM 生成路径
# ────────────────────────────────────────────

def _llm_generate_hypotheses(context: dict) -> list[Hypothesis]:
    """调用 LLM 生成假设"""
    from llm.client import LLMClient

    client = LLMClient()
    response = client.chat(
        "d1_hypothesis",
        {
            "indicator": context["indicator"],
            "deviation": context["deviation"],
            "macro_facts": context["macro_facts"],
            "industry_tags": context["industry_tags"],
        },
    )

    raw = response if isinstance(response, str) else str(response)
    return _parse_llm_hypothesis_result(raw)


def _parse_llm_hypothesis_result(raw: str) -> list[Hypothesis]:
    """解析 LLM 返回的假设列表"""
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            data = [data]
        return [
            Hypothesis(
                hypothesis=d.get("hypothesis", ""),
                reasoning=d.get("reasoning", d.get("logic", "")),
                source=d.get("source", "LLM推演"),
            )
            for d in data[:4]  # 最多 4 条
        ]
    except (json.JSONDecodeError, TypeError, KeyError) as e:
        logger.warning(f"解析 LLM 假设结果失败: {e}")
        return []


# ────────────────────────────────────────────
# 模板降级路径（无 LLM 时）
# ────────────────────────────────────────────

def _template_hypotheses(
    anomaly: Any,
    macro_facts: list[str],
    tags: Optional[CompanyTags],
    deviation: Optional[Any],
) -> list[Hypothesis]:
    """无 LLM 时的模板化假设生成

    基于常见商业常识模板生成互斥假设。
    """
    indicator = _extract_name(anomaly)

    # 通用假设模板
    templates = {
        "存货周转率": [
            ("产品滞销，市场需求疲软", "偏离度高 → 周转慢 → 库存积压"),
            ("战略性备货，预期原材料涨价", "提前囤货 → 库存增加 → 周转率下降"),
            ("新产线投产，产能爬坡期库存增加", "扩产初期库存临时升高"),
            ("会计政策变更，存货计价方法调整", "非经营因素导致数据波动"),
        ],
        "毛利率": [
            ("原材料成本上涨，挤压毛利空间", "上游涨价 → 成本上升 → 毛利率下降"),
            ("产品结构升级，高毛利产品占比提升", "产品组合变化 → 综合毛利率提升"),
            ("行业价格战，以价换量", "竞争加剧 → 降价促销 → 毛利率下降"),
            ("汇率波动影响出口毛利率", "人民币升值 → 出口产品毛利率下降"),
        ],
        "净现比": [
            ("行业特性：重资产行业折旧大，看似有利润实则无现金", "折旧摊销不消耗现金 → 净现比低为行业常态"),
            ("激进确认收入，应收账款大增", "提前确认收入 → 账面利润增加但现金未到账"),
            ("大量备货导致经营现金流出", "存货大幅增加 → 现金流恶化"),
            ("下游客户回款周期延长", "行业景气度下降 → 客户拖延付款"),
        ],
        "存贷双高": [
            ("集团资金集中管理模式", "众多子公司 → 合并报表体现高存款，同时集团统借统还"),
            ("财务造假：虚构货币资金", "大股东挪用 → 账面现金实际不存在"),
            ("限制性资金占比高", "保证金/质押存单 → 名义现金多但实际不可动用"),
            ("房地产/建筑行业特性", "项目公司独立融资 → 合并层面存贷双高属正常"),
        ],
        "母子资金分离度": [
            ("子公司资金归集到集团财务公司", "正常集团管理模式 → 母公司资金较少"),
            ("海外子公司受外汇管制", "资金无法自由汇回 → 合并层面资金虚高"),
            ("大股东资金占用/关联交易", "通过子公司为大股东提供资金 → 造假嫌疑"),
        ],
    }

    # 默认模板
    default = [
        ("宏观因素导致的变化", "基于宏观事实推断"),
        ("行业周期变化所致", "基于行业标签推断"),
        ("公司自身经营策略调整", "内部因素导致的变化"),
    ]

    candidates = templates.get(indicator, default)

    # 如果有关键词宏事实，调整第一条假设
    if macro_facts:
        candidates[0] = (f"受宏观因素影响：{macro_facts[0][:50]}...", "基于宏观事实")

    return [
        Hypothesis(hypothesis=h, reasoning=r, source="模板推演")
        for h, r in candidates[:4]
    ]
