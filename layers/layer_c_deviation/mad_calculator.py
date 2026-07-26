"""MAD 核心算法实现——纯数学计算"""

import numpy as np
from typing import Optional
from schemas.financial import FinancialStatement
from schemas.benchmark import Benchmark
from schemas.anomaly import DeviationAnomaly


def calc_mad(values: list[float]) -> float:
    """计算中位数绝对偏差（Median Absolute Deviation）

    MAD = median(|x_i - median(x)|)
    比标准差更稳健，不受极端值影响
    """
    arr = np.array(values)
    median = np.median(arr)
    mad = np.median(np.abs(arr - median))
    return float(mad)


def calc_deviation_multiple(
    actual_value: float,
    peer_values: list[float],
) -> tuple[float, float, float]:
    """计算实际值偏离同行中位数的 MAD 倍数

    返回：(peer_median, mad, deviation_multiple)
    """
    peer_array = np.array(peer_values)
    peer_median = float(np.median(peer_array))
    mad = calc_mad(peer_values)

    if mad == 0:
        return peer_median, 0.0, float("inf")

    deviation_multiple = abs(actual_value - peer_median) / mad
    return peer_median, mad, deviation_multiple


def run_deviation_analysis(
    financials: FinancialStatement,
    benchmark: Benchmark,
    mad_threshold: float = 2.0,
    extreme_threshold: float = 5.0,
) -> list[DeviationAnomaly]:
    """对所有财务指标计算 MAD 偏离度

    返回超过阈值的异常清单
    """
    anomalies = []

    for indicator, peer_median in benchmark.peer_median.items():
        actual = _get_field_value(financials, indicator)
        if actual is None:
            continue

        peer_values = _get_peer_values(benchmark, indicator)
        if not peer_values:
            continue

        median, mad, multiple = calc_deviation_multiple(actual, peer_values)

        if multiple >= mad_threshold:
            severity = "extreme" if multiple >= extreme_threshold else "abnormal"
            anomalies.append(DeviationAnomaly(
                indicator=indicator,
                actual_value=actual,
                benchmark_value=median,
                mad_multiple=multiple,
                severity=severity,
            ))

    return anomalies


def _get_field_value(financials: FinancialStatement, field: str) -> Optional[float]:
    """从财务数据中提取字段值"""
    for statement in [financials.balance_sheet, financials.income_statement, financials.cashflow]:
        if field in statement:
            return statement[field].value
    return None


def _get_peer_values(benchmark: Benchmark, indicator: str) -> list[float]:
    """从同行公司中提取某指标的值列表"""
    values = []
    for peer in benchmark.peer_companies:
        if indicator in peer.financials:
            values.append(peer.financials[indicator])
    return values
