"""
==========================================================
 schemas/benchmark.py — A层输出: 行业基准和同行对比的数据结构
==========================================================

A2 层匹配到相似企业后，计算出的基准数据。

包含两类基准: 
- 横向基准: 同行业 3~5 家最相似企业的各项财务指标中位数
- 纵向基准: 该公司自身过去 5 年 (剔除异常年份) 的数据均值

数据流向: 
- Benchmark -> C层 (MAD偏差计算需要同行中位数作为参照) 
- Benchmark -> E层 (最终报告中展示同行对比) 
"""

from pydantic import BaseModel
from typing import Optional

# 横向基准的最小有效同行数。
# 少于这个数，robust scale (MAD/Sn) 的估计方差大到不足以支撑"偏离 N 倍"
# 这种判断 —— n=1 时 MAD 恒为 0，任何差异都会被算成无穷倍。
# A2 用它决定 Benchmark.sample_sufficient，C 层用它作为硬门槛。
MIN_PEER_SAMPLE = 5


class PeerCompany(BaseModel):
    """匹配到的一家同行公司"""
    name: str                                    # 公司名称
    stock_code: str                              # 股票代码
    similarity_score: float                      # 软标签匹配相似度 (0~1) 
    financials: dict[str, float]                 # 关键财务指标 {指标名: 值}


class IndustryProfile(BaseModel):
    """被分析公司所在的行业特征"""
    industry_name: str                           # 行业名称 (如 白酒) 
    hard_tag_system: str = "申万三级行业"         # 标签分类体系


class Benchmark(BaseModel):
    """横向 + 纵向基准完整数据"""
    industry: IndustryProfile                    # 行业信息
    peer_median: dict[str, float]                # 同行中位数 {指标名: 值}
    historical_mean: dict[str, float]            # 该公司5年均值 {指标名: 值}
    peer_companies: list[PeerCompany]            # 参与对比的同行列表

    # 基准的样本可信度。样本退化时仍然算得出数字，但那个数字没有统计意义，
    # 必须由 A2 如实传下来，而不是让 C/E 层靠"结果看起来正常"去猜。
    # 曾把目标公司自己放进同行池，池子里只有它一家 -> 用自己当自己的基准，
    # 所有指标的偏离倍数都变成 inf -> 全部判 extreme -> 100 分 + 高置信度。
    peer_sample_size: int = 0                    # 实际参与基准计算的公司数（不含目标公司自身）
    sample_sufficient: bool = True               # 是否达到 MIN_PEER_SAMPLE
