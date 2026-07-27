"""
==========================================================
 schemas/benchmark.py — A层输出：行业基准和同行对比的数据结构
==========================================================

A2 层匹配到相似企业后，计算出的基准数据。

包含两类基准：
- 横向基准：同行业 3~5 家最相似企业的各项财务指标中位数
- 纵向基准：该公司自身过去 5 年（剔除异常年份）的数据均值

数据流向：
- Benchmark → C层（MAD偏差计算需要同行中位数作为参照）
- Benchmark → E层（最终报告中展示同行对比）
"""

from pydantic import BaseModel
from typing import Optional


class PeerCompany(BaseModel):
    """匹配到的一家同行公司"""
    name: str                                    # 公司名称
    stock_code: str                              # 股票代码
    similarity_score: float                      # 软标签匹配相似度（0~1）
    financials: dict[str, float]                 # 关键财务指标 {指标名: 值}


class IndustryProfile(BaseModel):
    """被分析公司所在的行业特征"""
    industry_name: str                           # 行业名称（如 白酒）
    hard_tag_system: str = "同花顺三级行业"       # 标签分类体系
    soft_tag_summary: str = ""                   # 行业共性的软标签描述


class Benchmark(BaseModel):
    """横向 + 纵向基准完整数据"""
    industry: IndustryProfile                    # 行业信息
    peer_median: dict[str, float]                # 同行中位数 {指标名: 值}
    historical_mean: dict[str, float]            # 该公司5年均值 {指标名: 值}
    peer_companies: list[PeerCompany]            # 参与对比的同行列表
