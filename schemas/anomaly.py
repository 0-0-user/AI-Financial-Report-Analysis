"""
==========================================================
 schemas/anomaly.py — B+层 / C层输出: 异常检测结果的数据结构
==========================================================

本文件定义了系统检测到的两类异常数据结构: 
- B+层异常 (逻辑异常) : 财报数据自身的"逻辑硬伤"
  如: 净现比过低 (利润含金量低) 、存贷双高、子公司资金分离
  特征: 不下定论，只列清单，最终判断交给 D 层

- C层异常 (偏差异常) : 该公司的指标偏离同行平均水平多远
  特征: 不判断好坏，只用 MAD 算法算偏离倍数

数据流向: 
- LogicAnomaly + DeviationAnomaly -> D层 (查找原因并分配概率) 
- LogicAnomaly + DeviationAnomaly -> E层 (计算扣分) 
"""

from pydantic import BaseModel
from typing import Optional


class LogicAnomaly(BaseModel):
    """B+层: 一条逻辑异常检查结果"""
    check_name: str                              # 检查项 (net_profit_cash_ratio / deposit_loan_dual_high / fund_separation) 
    value: float                                 # 实际计算值
    threshold: float                             # 正常阈值
    severity: float                              # 严重度评分 (供E层用) 
    summary: str                                 # 一句话描述
    detail: Optional[str] = None                 # 详细说明


class DeviationAnomaly(BaseModel):
    """C层: 一个MAD偏差异常结果"""
    indicator: str                               # 指标名 (如 inventory_turnover) 
    actual_value: float                          # 该公司实际值
    benchmark_value: float                       # 同行中位数
    mad_multiple: float                          # 偏离中位数的MAD倍数
    severity: str = "abnormal"                   # normal / abnormal / extreme


class LogicAnomalyList(BaseModel):
    """B+层完整输出"""
    anomalies: list[LogicAnomaly]


class DeviationList(BaseModel):
    """C层完整输出"""
    anomalies: list[DeviationAnomaly]
