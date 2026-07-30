"""
==========================================================
 schemas/report.py — E层输出：最终评分和报告的数据结构
==========================================================

E 层将前面所有层的分析结果汇总，输出最终的结构化报告。

报告包含三个核心模块：
1. 【整体研判】  综合得分 + 置信度等级 + 同行对比
2. 【核心异常与概率归因】  异常指标 + 概率分布 + 判断依据
3. 【多空逻辑栈】  看多支持点 + 看空风险点

整体流程：
  E1（纯代码算分）→ E2（组装报告，含少量 LLM 文本润色）
"""

from pydantic import BaseModel
from typing import Optional


class PeerComparison(BaseModel):
    """同行对比条目"""
    company_name: str                            # 公司名
    similarity_score: float                      # 相似度


class CauseDetail(BaseModel):
    """单个归因原因的影响明细"""
    cause: str                               # 原因描述（用户看到的完整句子）
    probability: float                       # D-S 融合概率
    score_base: int                          # 语义定性分 (-3~+2)
    matched_keywords: list[str]              # 匹配到的关键词（内部使用）
    effective_score: float                   # 有效影响值 = score_base × (1-K)


class AnomalyScoreDetail(BaseModel):
    """单个异常指标的评分明细"""
    indicator: str                           # 指标名
    source: str                              # "B+" / "C"
    w_phe: float                             # 现象严重性系数
    k_value: float                           # D-S 冲突系数
    delta: float                             # 1 - K
    causes: list[CauseDetail]                # 各原因明细
    anomaly_score: float                     # 该异常扣分


class ScoreBreakdown(BaseModel):
    """E1层：打分明细（新公式版）"""
    base_score: float = 100                  # 基础分
    anomaly_details: list[AnomalyScoreDetail] = []  # 每个异常的评分明细
    total_deduction: float = 0               # 总扣分
    final_score: float = 0                   # 最终得分


class OverallAssessment(BaseModel):
    """【整体研判】模块"""
    score: float                                 # 综合得分
    confidence_tier: str                         # 高置信度 / 中等置信度 / 低置信度
    peer_comparisons: list[PeerComparison]       # 同行对比列表


class AnomalyEvidence(BaseModel):
    """异常判断依据"""
    source_excerpt: str                          # 原文摘要
    page_number: int = 0                        # 所在页码


class CoreAnomaly(BaseModel):
    """【核心异常与概率归因】模块条目"""
    indicator: str                               # 异常指标
    source: str                                  # 来源（B+ / C）
    probabilities: dict[str, float]              # 概率分布
    evidence: list[AnomalyEvidence]              # 判断依据
    severity: Optional[str] = None               # 严重程度


class Report(BaseModel):
    """E2层：完整结构化报告"""
    company_name: str                            # 公司名称
    stock_code: str                              # 股票代码
    report_year: int                             # 分析年份
    overall_assessment: OverallAssessment        # 模块1：整体研判
    core_anomalies: list[CoreAnomaly]           # 模块2：核心异常与概率归因
    bull_points: list[str]                       # 模块3：看多支持点
    bear_points: list[str]                       # 模块3：看空风险点
    score_breakdown: Optional[ScoreBreakdown] = None  # 评分明细
    report_generated_at: Optional[str] = None    # 报告生成时间
