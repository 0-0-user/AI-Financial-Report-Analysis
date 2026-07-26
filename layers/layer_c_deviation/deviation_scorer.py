"""异常指标筛选与排序工具"""

from schemas.anomaly import DeviationAnomaly


def rank_by_severity(deviations: list[DeviationAnomaly]) -> list[DeviationAnomaly]:
    """按严重程度排序（极端优先）"""
    severity_order = {"extreme": 0, "abnormal": 1, "normal": 2}
    return sorted(deviations, key=lambda d: severity_order.get(d.severity, 99))


def filter_by_threshold(
    deviations: list[DeviationAnomaly],
    min_multiple: float = 2.0,
) -> list[DeviationAnomaly]:
    """筛选超过指定 MAD 倍数的异常"""
    return [d for d in deviations if d.mad_multiple >= min_multiple]
