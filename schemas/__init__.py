"""
==========================================================
 schemas 包 — 数据契约（接口协议）
==========================================================

本包定义了整个系统中各层之间传递数据的标准结构（Pydantic 模型）。
所有层（包括队友负责的 layers/）都基于这里的定义进行数据交换。

作用：
- 统一字段名，避免各层各自取名导致对接不上
- 提供类型校验，运行时发现数据格式错误
- 作为"接口文档"，新成员看这里就知道数据长什么样

使用方式：
    from schemas import RawDocument, FinancialStatement, Report
"""

from .enums import ReportType, AnomalySeverity, ConfidenceTier

from .raw_doc import (
    RawTableRow,
    FinancialTable,
    DiscussionSection,
    ManagementDiscussion,
    FootnoteItem,
    Footnotes,
    CompanyOverview,
    DocumentMetadata,
    RawDocument,
)

from .b0_guide import FieldMapping, ColumnHeader, TableGuide, B0Guide

from .financial import FinancialField, ValidationCheck, ValidationResult, FinancialStatement

from .tags import HardTag, FinancialProfile, CompanyTags

from .anomaly import LogicAnomaly, DeviationAnomaly, LogicAnomalyList, DeviationList

from .benchmark import PeerCompany, IndustryProfile, Benchmark

from .reasoning import Hypothesis, Explanation, DualPathResult, ProbabilityAssignment

from .report import PeerComparison, ScoreBreakdown, OverallAssessment, AnomalyEvidence, CoreAnomaly, Report

__all__ = [
    # enums
    "ReportType",
    "AnomalySeverity",
    "ConfidenceTier",
    # raw_doc
    "RawTableRow",
    "FinancialTable",
    "DiscussionSection",
    "ManagementDiscussion",
    "FootnoteItem",
    "Footnotes",
    "CompanyOverview",
    "DocumentMetadata",
    "RawDocument",
    # b0_guide
    "FieldMapping",
    "ColumnHeader",
    "TableGuide",
    "B0Guide",
    # financial
    "FinancialField",
    "ValidationCheck",
    "ValidationResult",
    "FinancialStatement",
    # tags
    "HardTag",
    "FinancialProfile",
    "CompanyTags",
    # anomaly
    "LogicAnomaly",
    "DeviationAnomaly",
    "LogicAnomalyList",
    "DeviationList",
    # benchmark
    "PeerCompany",
    "IndustryProfile",
    "Benchmark",
    # reasoning
    "Hypothesis",
    "Explanation",
    "DualPathResult",
    "ProbabilityAssignment",
    # report
    "PeerComparison",
    "ScoreBreakdown",
    "OverallAssessment",
    "AnomalyEvidence",
    "CoreAnomaly",
    "Report",
]
