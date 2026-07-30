"""
==========================================================
 schemas/report.py — E层输出: 最终评分和报告的数据结构
==========================================================

E 层将前面所有层的分析结果汇总，输出最终的结构化报告。

报告包含六个输出模块: 
0. 【宏观事实】  8 张折线图 + 关键指标横向对比
1. 【综合评级与得分区间】  E1 得分 + [Bel, Pl] 信任区间
2. 【核心异常指标清单】  B+/C 层异常 + 严重程度
3. 【解释】  D2 概率分布 + K 冲突系数 + footnotes
4. 【总结报告】  LLM 综合评述
5. 【证据溯源】  原因出处 + 年报页码

整体流程: 
  E1 (纯代码算分) -> E2 (组装报告，含少量 LLM 文本润色) 
"""

from pydantic import BaseModel
from typing import Optional


class PeerComparison(BaseModel):
    """同行对比条目"""
    company_name: str                            # 公司名
    similarity_score: float                      # 相似度


class CauseDetail(BaseModel):
    """单个归因原因的影响明细"""
    cause: str                               # 原因描述 (用户看到的完整句子) 
    probability: float                       # D-S 融合概率
    score_base: int                          # 语义定性分 (-3~+2)
    matched_keywords: list[str]              # 匹配到的关键词 (内部使用) 
    effective_score: float                   # 有效影响值 = score_base x (1-K)
    footnote_id: Optional[str] = None        # 脚注 ID，链接到证据溯源


class AnomalyScoreDetail(BaseModel):
    """单个异常指标的评分明细"""
    indicator: str                           # 指标名
    source: str                              # "B+" / "C"
    w_phe: float                             # 现象严重性系数
    k_value: float                           # D-S 冲突系数
    delta: float                             # 1 - K
    causes: list[CauseDetail]                # 各原因明细
    anomaly_score: float                     # 该异常扣分 (中心值) 
    score_bel: float = 0.0                   # 最悲观情景扣分 (Bel) 
    score_pl: float = 0.0                    # 最乐观情景扣分 (Pl) 


class ScoreBreakdown(BaseModel):
    """E1层: 打分明细 (新公式版) """
    base_score: float = 100                  # 基础分
    anomaly_details: list[AnomalyScoreDetail] = []  # 每个异常的评分明细
    total_deduction: float = 0               # 总扣分
    final_score: float = 0                   # 最终得分


class TrustInterval(BaseModel):
    """模块1: 信任区间 [Bel, Pl]"""
    center_score: float                      # E1 中心得分
    lower_bound: float                       # Bel (最悲观) 
    upper_bound: float                       # Pl (最乐观) 
    avg_m_theta: float                       # 平均 m(Θ) 不确定性
    avg_k: float                             # 平均 K 冲突系数


class OverallAssessment(BaseModel):
    """【整体研判】模块"""
    score: float                                 # 综合得分
    confidence_tier: str                         # 高置信度 / 中等置信度 / 低置信度
    peer_comparisons: list[PeerComparison]       # 同行对比列表


class AnomalyEvidence(BaseModel):
    """异常判断依据"""
    source_excerpt: str                          # 原文摘要
    page_number: int = 0                        # 所在页码
    source_type: str = "年报原文"                 # 来源类型


class CoreAnomaly(BaseModel):
    """【核心异常与概率归因】模块条目 (模块2 + 模块3) """
    indicator: str                               # 异常指标
    source: str                                  # 来源 (B+ / C) 
    probabilities: dict[str, float]              # 概率分布
    evidence: list[AnomalyEvidence]              # 判断依据
    severity: Optional[str] = None               # 严重程度
    k_value: float = 0.0                         # D-S 冲突系数
    anomaly_score_detail: Optional[AnomalyScoreDetail] = None  # 评分明细


class EvidenceSource(BaseModel):
    """模块5: 一条证据溯源条目"""
    footnote_id: str                             # 脚注 ID，如 fn_001
    cause: str                                   # 归因原因
    source_type: str                             # "年报原文" / "推演假设"
    source_text: str                             # 原文引用 / 推演逻辑
    page_number: Optional[int] = None            # 年报页码
    hypothesis_reasoning: Optional[str] = None   # 推演假设的推理逻辑


class ChartConfig(BaseModel):
    """模块0: 一张折线图的配置"""
    indicator_name: str                          # 指标中文名 (如"销售毛利率") 
    akshare_column: str                          # akshare 列名
    filename: str                                # PNG 文件名
    unit: str = "%"                              # 单位
    y_label: str = ""                            # 纵轴标签


class Report(BaseModel):
    """E2层: 完整结构化报告 (6 模块) """
    company_name: str                            # 公司名称
    stock_code: str                              # 股票代码
    report_year: int                             # 分析年份

    # 模块0: 宏观事实
    chart_configs: list[ChartConfig] = []        # 图表配置列表
    chart_base_path: str = "charts"              # 图表文件相对路径

    # 模块1: 综合评级
    overall_assessment: OverallAssessment        # 综合得分 + 置信度
    trust_interval: Optional[TrustInterval] = None  # [Bel, Pl] 信任区间

    # 模块2 + 模块3: 核心异常与解释
    core_anomalies: list[CoreAnomaly]           # 异常清单 + 概率归因

    # 模块3 自由文本 (LLM 格式化后的解释段落) 
    explanation_text: Optional[str] = None

    # 模块4: 总结报告 (LLM 生成) 
    summary_text: Optional[str] = None

    # 模块5: 证据溯源
    evidence_sources: list[EvidenceSource] = []

    # 评分明细
    score_breakdown: Optional[ScoreBreakdown] = None

    # 多空逻辑栈 (保留兼容) 
    bull_points: list[str] = []
    bear_points: list[str] = []

    # 元信息
    report_generated_at: Optional[str] = None
