"""
==========================================================
 schemas/reasoning.py — D层输出：推理和概率分配的数据结构
==========================================================

D 层是整个系统中 LLM 调用最密集的区域。对于每一个异常指标，
系统执行双路径推理：

路1（查原文）：大模型读年报的【附注】和【管理层讨论】原文，
               提取客观解释。如果原文含糊则标记为模糊。

路2（推演假设）：大模型不看原文，而是基于宏观事实 + 行业标签 +
                偏差数据，发散出几种合理的假设。

最后 D2 层将两路结果合并，对每个异常给出概率化归因。

主要类型：
- Hypothesis:            一条推演假设（假设内容 + 推理逻辑 + 依据来源）
- Explanation:           一条原文解释（摘要 + 原文引用 + 页码 + 是否模糊）
- DualPathResult:        D1层中间结果（单个异常的两路推理结果）
- ProbabilityAssignment: 最终概率分配（异常指标 + 归因概率映射）
"""

from pydantic import BaseModel
from typing import Union, Optional


class Hypothesis(BaseModel):
    """D1路2：一条推演假设"""
    hypothesis: str                              # 假设内容
    reasoning: str                               # 推演逻辑
    source: str                                  # 依据来源（宏观事实/行业标签/偏差数据）
    confidence_rank: int = 1                     # 置信度排序（1 = 最可信）


class Explanation(BaseModel):
    """D1路1：从年报原文提取的一条解释"""
    summary: str                                 # 解释摘要
    source_text: str                             # 原文引用
    page_number: int                             # 年报页码
    is_vague: bool = False                       # 是否含糊其辞
    confidence_rank: int = 1                     # 置信度排序（1 = 最可信，基于显要程度）


class DualPathResult(BaseModel):
    """D1层：单个异常的双路径推理中间结果"""
    anomaly_indicator: str                       # 异常指标名
    anomaly_source: str                          # B+ / C
    lookups: list[Explanation]                   # 路1结果（查原文）
    hypotheses: list[Hypothesis]                 # 路2结果（推演假设）


class ProbabilityAssignment(BaseModel):
    """D2层：单个异常的最终概率分配结果"""
    anomaly_indicator: str                       # 异常指标名
    anomaly_source: str                          # B+ / C
    lookups: list[Explanation]                   # 路1结果
    hypotheses: list[Hypothesis]                 # 路2结果
    probabilities: dict[str, float]              # 归因概率（pignistic 转换后，sum=1.0）
    reasoning_summary: Optional[str] = None      # 综合判断依据（已废弃，保留兼容）
    ds_metadata: Optional[dict] = None           # D-S 证据理论元数据
