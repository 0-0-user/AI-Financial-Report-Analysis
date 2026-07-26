"""层间数据传递上下文——贯穿全流程的数据背包"""

from dataclasses import dataclass, field
from typing import Optional

from schemas.raw_doc import RawDocument
from schemas.tags import CompanyTags
from schemas.benchmark import Benchmark
from schemas.financial import FinancialStatement
from schemas.anomaly import LogicAnomaly, DeviationAnomaly
from schemas.reasoning import ProbabilityAssignment
from schemas.report import ScoreBreakdown, Report


@dataclass
class PipelineContext:
    """每一层的输出都往里放，后一层从里面取"""

    # 第 0 层
    raw_doc: Optional[RawDocument] = None

    # A 层
    macro_facts: Optional[list[str]] = None
    tags: Optional[CompanyTags] = None
    benchmark: Optional[Benchmark] = None

    # B 层
    financials: Optional[FinancialStatement] = None
    parent_financials: Optional[FinancialStatement] = None
    validation_passed: bool = False

    # B+ 层
    logic_anomalies: Optional[list[LogicAnomaly]] = None

    # C 层
    deviations: Optional[list[DeviationAnomaly]] = None

    # D 层
    reasoning_results: Optional[list[ProbabilityAssignment]] = None

    # E 层
    score: Optional[ScoreBreakdown] = None
    report: Optional[Report] = None

    # 元信息
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
