from pydantic import BaseModel


class Hypothesis(BaseModel):
    """D1路2：一条推演假设"""
    hypothesis: str
    reasoning: str
    source: str  # "宏观事实" / "行业标签" / "偏差数据"


class Explanation(BaseModel):
    """D1路1：从原文提取的解释"""
    summary: str
    source_text: str
    page_number: int
    is_vague: bool


class ProbabilityAssignment(BaseModel):
    """D2层：一个异常的概率分配结果"""
    anomaly_indicator: str
    anomaly_source: str  # "B+" / "C"
    lookups: list[Explanation]
    hypotheses: list[Hypothesis]
    probabilities: dict[str, float]
