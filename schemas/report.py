from pydantic import BaseModel
from typing import Optional


class ScoreBreakdown(BaseModel):
    """E1层：打分明细"""
    base_score: float
    c_layer_deduction: float
    bplus_layer_deduction: float
    final_score: float


class Report(BaseModel):
    """E2层：完整报告"""
    company_name: str
    stock_code: str
    report_year: int
    overall_assessment: dict
    core_anomalies: list[dict]
    bull_points: list[str]
    bear_points: list[str]
    score_breakdown: Optional[ScoreBreakdown] = None
    report_generated_at: Optional[str] = None
