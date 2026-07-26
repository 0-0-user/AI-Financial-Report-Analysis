from pydantic import BaseModel


class LogicAnomaly(BaseModel):
    """B+层：逻辑异常检查结果"""
    check_name: str
    value: float
    threshold: float
    severity: float
    summary: str


class DeviationAnomaly(BaseModel):
    """C层：MAD 偏差异常"""
    indicator: str
    actual_value: float
    benchmark_value: float
    mad_multiple: float
    severity: str  # "normal" / "abnormal" / "extreme"
