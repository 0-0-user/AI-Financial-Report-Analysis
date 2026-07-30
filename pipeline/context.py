"""
==========================================================
 pipeline/context.py — 层间数据传递上下文
==========================================================

PipelineContext 是一个贯穿全流程的"数据背包"。
每一层执行完毕后，将输出结果存入 context 中对应的字段。
下一层执行时，从 context 读取需要的输入数据。

这种设计的好处：
- 每一层只需要关注自己的输入/输出，不需要知道其他层的细节
- 方便调试：可以打印整个 context 查看中间结果
- 支持跳过：调试时可以只跑某几层

使用方式：
    ctx = PipelineContext()
    ctx.raw_doc = RawDocument(...)   # 第0层写
    ctx.tags = CompanyTags(...)      # A层写
    # ...
"""

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

    # 多年财务原始数据（供 E2 图表使用，由 A2 层填充）
    multi_year_financials: Optional[dict[str, list[dict]]] = None
    multi_year_company_names: Optional[dict[str, str]] = None

    # 元信息
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
