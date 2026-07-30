"""C层: 异常指标筛选与排序 (v2: Sn/MAD 统一排序 + 分组报告) """

import numpy as np
from schemas.anomaly import DeviationAnomaly


def rank_by_severity(deviations: list[DeviationAnomaly]) -> list[DeviationAnomaly]:
    """按严重程度排序 (极端优先，同时考虑偏离倍数) """
    severity_order = {"extreme": 0, "abnormal": 1, "normal": 2}
    return sorted(
        deviations,
        key=lambda d: (severity_order.get(d.severity, 99), -d.mad_multiple),
    )


def filter_by_threshold(
    deviations: list[DeviationAnomaly], min_multiple: float = 2.0,
) -> list[DeviationAnomaly]:
    """筛选超阈值异常"""
    return [d for d in deviations if d.mad_multiple >= min_multiple]


def group_by_category(deviations: list[DeviationAnomaly]) -> dict[str, list[DeviationAnomaly]]:
    """按指标类别分组

    依据 config/weights.yaml 中的 category 字段。
    未在权重库中定义的归入"其他"。
    """
    try:
        from pathlib import Path
        import yaml
        cfg = yaml.safe_load(Path("config/weights.yaml").read_text(encoding="utf-8"))
        weights = cfg.get("weights", {})
    except Exception:
        weights = {}

    groups: dict[str, list[DeviationAnomaly]] = {}
    for d in deviations:
        cat = weights.get(d.indicator, {}).get("category", "其他")
        groups.setdefault(cat, []).append(d)
    return groups


def summary_report(deviations: list[DeviationAnomaly]) -> str:
    """生成可读摘要 (供日志/调试) """
    if not deviations:
        return "C层: 无异常指标"
    ranked = rank_by_severity(deviations)
    extreme = sum(1 for d in ranked if d.severity == "extreme")
    abnormal = sum(1 for d in ranked if d.severity == "abnormal")
    lines = [f"C层: {len(ranked)} 个异常 (极端{extreme}，一般{abnormal}) "]
    for d in ranked[:10]:
        lines.append(f"  {d.indicator}: {d.mad_multiple:.1f}x [{d.severity}] "
                     f"actual={d.actual_value:.2f} vs median={d.benchmark_value:.2f}")
    return "\n".join(lines)
