"""
==========================================================
 tests/test_pipeline/test_full_pipeline.py — 流水线集成测试
==========================================================

端到端测试完整流水线的三个核心场景：

1. 正常路径：构造完整 mock 数据（A→B→B+→C→D→E），
   验证 E1 评分和 E2 报告输出格式正确

2. 阻断路径：B1 层勾稽校验失败时，输出异常阻断报告

3. 空数据路径：没有任何异常时，输出空报告（100 分）

每个测试都直接构造 PipelineContext，绕过 stubs 直接验证
数据契约和评分逻辑的正确性。
"""

import pytest
from pipeline.context import PipelineContext
from schemas.tags import HardTag, FinancialProfile, CompanyTags
from schemas.benchmark import IndustryProfile, Benchmark, PeerCompany
from schemas.financial import FinancialStatement, FinancialField, ValidationResult, ValidationCheck
from schemas.anomaly import DeviationAnomaly, LogicAnomaly
from schemas.reasoning import ProbabilityAssignment, Explanation, Hypothesis
from schemas.report import Report, ScoreBreakdown
from layers.layer_e_output.e1_scoring import run_scoring
from layers.layer_e_output.e2_report_gen import generate_report


def _make_full_context() -> PipelineContext:
    """构造一份完整的 mock PipelineContext（正常数据）"""
    ctx = PipelineContext()

    # ── A 层数据 ──
    ctx.tags = CompanyTags(
        company_name="贵州茅台",
        stock_code="600519",
        hard_tags=[
            HardTag(system="同花顺三级行业", value="白酒"),
            HardTag(system="同花顺二级行业", value="饮料制造"),
        ],
        financial_profile=FinancialProfile(
            levels=["极高", "极高", "低", "低", "低", "高"],
        ),
    )
    ctx.macro_facts = [
        "2024年白酒行业产量同比下降2.5%",
        "高端白酒批价在2024年保持稳定",
        "居民消费升级趋势持续，高端白酒需求韧性较强",
    ]
    ctx.benchmark = Benchmark(
        industry=IndustryProfile(industry_name="白酒"),
        peer_median={"存货周转率": 0.8, "毛利率": 0.75, "净利率": 0.30, "资产负债率": 0.35, "营业收入增长率": 0.15},
        historical_mean={"存货周转率": 0.75, "毛利率": 0.76, "净利率": 0.28, "资产负债率": 0.30, "营业收入增长率": 0.18},
        peer_companies=[
            PeerCompany(name="五粮液", stock_code="000858", similarity_score=0.95, financials={}),
            PeerCompany(name="泸州老窖", stock_code="000568", similarity_score=0.88, financials={}),
            PeerCompany(name="洋河股份", stock_code="002304", similarity_score=0.82, financials={}),
        ],
    )

    # ── B 层数据 ──
    ctx.financials = FinancialStatement(
        company_name="贵州茅台",
        stock_code="600519",
        year=2024,
        report_type="合并报表",
        balance_sheet={
            "Total_Assets": FinancialField(standard_name="Total_Assets", raw_name="资产总计", value=262_000_000_000, original_unit="元", report_type="合并报表"),
            "Total_Liabilities": FinancialField(standard_name="Total_Liabilities", raw_name="负债合计", value=52_000_000_000, original_unit="元", report_type="合并报表"),
            "Total_Equity": FinancialField(standard_name="Total_Equity", raw_name="所有者权益合计", value=210_000_000_000, original_unit="元", report_type="合并报表"),
            "Monetary_Funds": FinancialField(standard_name="Monetary_Funds", raw_name="货币资金", value=80_000_000_000, original_unit="元", report_type="合并报表"),
        },
        income_statement={
            "Revenue_Total": FinancialField(standard_name="Revenue_Total", raw_name="营业收入", value=150_000_000_000, original_unit="元", report_type="合并报表"),
            "Net_Profit": FinancialField(standard_name="Net_Profit", raw_name="净利润", value=75_000_000_000, original_unit="元", report_type="合并报表"),
            "Gross_Profit": FinancialField(standard_name="Gross_Profit", raw_name="毛利润", value=120_000_000_000, original_unit="元", report_type="合并报表"),
        },
        cashflow={
            "Operating_Cash_Flow": FinancialField(standard_name="Operating_Cash_Flow", raw_name="经营活动现金流净额", value=70_000_000_000, original_unit="元", report_type="合并报表"),
        },
        validation=ValidationResult(is_valid=True, checks=[
            ValidationCheck(check_name="资产负债表恒等式", passed=True, left_value=262_000_000_000, right_value=262_000_000_000, detail="通过"),
        ]),
    )
    ctx.validation_passed = True

    # ── B+ 层数据（无逻辑异常）──
    ctx.logic_anomalies = []

    # ── C 层数据（两个偏差异常）──
    ctx.deviations = [
        DeviationAnomaly(
            indicator="存货周转率",
            actual_value=0.3,
            benchmark_value=0.8,
            mad_multiple=3.5,  # 显著偏离
            severity="extreme",
        ),
        DeviationAnomaly(
            indicator="营业收入增长率",
            actual_value=0.05,
            benchmark_value=0.15,
            mad_multiple=2.2,  # 一般偏离
            severity="abnormal",
        ),
    ]

    # ── D 层数据（概率归因）──
    ctx.reasoning_results = [
        ProbabilityAssignment(
            anomaly_indicator="存货周转率",
            anomaly_source="C",
            lookups=[
                Explanation(summary="存货增加主要因系列酒备货", source_text="存货增加主要系本公司加大系列酒备货所致", page_number=120, is_vague=False),
            ],
            hypotheses=[
                Hypothesis(hypothesis="战略备货", reasoning="高端白酒供不应求，公司主动增加库存", source="行业数据"),
                Hypothesis(hypothesis="产品滞销", reasoning="经济下行导致高端白酒需求减弱", source="宏观事实"),
            ],
            probabilities={"战略备货": 0.6, "经营滞销": 0.3, "其他": 0.1},
        ),
        ProbabilityAssignment(
            anomaly_indicator="营业收入增长率",
            anomaly_source="C",
            lookups=[],
            hypotheses=[
                Hypothesis(hypothesis="基数效应", reasoning="前两年高速增长后回归常态", source="历史数据"),
                Hypothesis(hypothesis="需求放缓", reasoning="宏观经济压力下消费疲软", source="宏观事实"),
            ],
            probabilities={"基数效应": 0.5, "需求放缓": 0.4, "其他": 0.1},
        ),
    ]

    return ctx


class TestFullPipeline:
    """端到端流水线集成测试"""

    def test_pipeline_runs_with_mock(self):
        """正常 mock 数据下，E1+E2 应输出完整报告"""
        ctx = _make_full_context()

        # 执行 E1 打分
        ctx.score = run_scoring(ctx)
        assert isinstance(ctx.score, ScoreBreakdown)
        assert ctx.score.final_score > 0
        assert ctx.score.final_score <= 100

        # 执行 E2 报告组装
        report = generate_report(ctx)
        assert isinstance(report, Report)

        # 验证报告完整性
        assert report.company_name == "贵州茅台"
        assert report.stock_code == "600519"
        assert report.report_year == 2024
        assert report.overall_assessment.score == ctx.score.final_score
        assert len(report.core_anomalies) == 2  # 两个偏差异常
        assert len(report.bull_points) >= 0
        assert len(report.bear_points) >= 1  # 至少一个利空
        assert report.report_generated_at is not None

    def test_pipeline_output_format(self):
        """报告结构应完全符合 Report schema"""
        ctx = _make_full_context()
        ctx.score = run_scoring(ctx)
        report = generate_report(ctx)

        # 验证整体研判
        oa = report.overall_assessment
        assert 0 <= oa.score <= 100
        assert oa.confidence_tier in ("高置信度", "中等置信度", "低置信度")
        assert len(oa.peer_comparisons) == 3

        # 验证核心异常列表
        for anomaly in report.core_anomalies:
            assert isinstance(anomaly.indicator, str)
            assert anomaly.source in ("B+", "C")
            assert len(anomaly.probabilities) > 0
            total_prob = sum(anomaly.probabilities.values())
            assert abs(total_prob - 1.0) < 0.01  # 概率总和应为 1.0
            if anomaly.evidence:
                for e in anomaly.evidence:
                    assert isinstance(e.source_excerpt, str)

        # 验证看多看空
        assert isinstance(report.bull_points, list)
        assert isinstance(report.bear_points, list)

    def test_error_handling(self):
        """B1 层校验失败时应输出阻断报告"""
        ctx = PipelineContext()
        ctx.validation_passed = False
        ctx.errors = ["B1层阻断：勾稽校验失败，PDF 数据源可能损坏或造假"]

        # Orchestrator 在错误时调用 _build_error_report
        from pipeline.orchestrator import Orchestrator
        orch = Orchestrator()
        error_report = orch._build_error_report(ctx)

        assert isinstance(error_report, Report)
        assert error_report.overall_assessment.score == 0
        assert error_report.overall_assessment.confidence_tier == "低置信度"
        assert error_report.company_name == ""
        assert len(error_report.core_anomalies) == 0

    def test_empty_data_graceful(self):
        """没有任何数据时应优雅降级，不崩溃"""
        ctx = PipelineContext()

        # 空数据运行评分
        score = run_scoring(ctx)
        assert score.final_score == 100  # 无异常 = 满分

        # 空数据生成报告
        report = generate_report(ctx)
        assert report.company_name == ""
        assert report.overall_assessment.score == 0
        assert report.overall_assessment.confidence_tier == "低置信度"
        assert len(report.core_anomalies) == 0
        # 空数据时 bull/bear 有默认提示文案
        assert len(report.bull_points) >= 0
        assert len(report.bear_points) >= 0
        # 不崩溃即可
        assert isinstance(report.report_generated_at, str)
