"""
==========================================================
 tests/test_layers/test_d1_reasoning.py — D1 双路径隔离测试
==========================================================

测试 D1 层两路的知识来源隔离：
- 路1（原文搬运）不接收任何 A 层数据
- 路2（外部推演）不接收金融画像、不接收年报原文
- LLM 失败时不做降级
- confidence_rank 排序正确
"""

import json
import pytest
from typing import Any

from schemas.anomaly import LogicAnomaly, DeviationAnomaly
from schemas.raw_doc import (
    RawDocument, DocumentMetadata, FinancialTable, ManagementDiscussion,
    DiscussionSection, Footnotes, FootnoteItem, CompanyOverview,
)
from schemas.tags import HardTag, CompanyTags
from schemas.reasoning import Explanation, Hypothesis


# ============================================================
# 辅助函数：构造 mock 数据
# ============================================================

def _make_deviation_anomaly(
    indicator="存货周转率",
    actual_value=0.3,
    mad_multiple=3.5,
) -> DeviationAnomaly:
    return DeviationAnomaly(
        indicator=indicator,
        actual_value=actual_value,
        benchmark_value=0.8,
        mad_multiple=mad_multiple,
        severity="extreme",
    )


def _make_logic_anomaly(
    check_name="net_profit_cash_ratio",
    value=0.3,
    threshold=0.6,
) -> LogicAnomaly:
    return LogicAnomaly(
        check_name=check_name,
        value=value,
        threshold=threshold,
        severity=2.0,
        summary="利润含金量低",
    )


def _make_minimal_raw_doc() -> RawDocument:
    return RawDocument(
        metadata=DocumentMetadata(
            file_name="test.pdf",
            page_count=100,
            report_year=2024,
        ),
        financial_data=FinancialTable(
            balance_sheet=[],
            income_statement=[],
            cashflow_statement=[],
        ),
        management_discussion=ManagementDiscussion(
            sections=[
                DiscussionSection(
                    title="经营情况讨论",
                    content="报告期内，公司营业收入增长主要得益于新市场开拓。"
                            "存货增加主要系加大备货力度所致。",
                    page_number=15,
                ),
                DiscussionSection(
                    title="核心竞争力分析",
                    content="公司拥有完善的销售网络和品牌优势。",
                    page_number=18,
                ),
            ],
        ),
        footnotes=Footnotes(
            items=[
                FootnoteItem(
                    name="存货附注",
                    content="存货期末余额较上年增加30%，主要系原材料备货增加。",
                    page_number=120,
                ),
            ],
        ),
        company_overview=CompanyOverview(
            company_name="测试公司",
            stock_code="000000",
            business_description="主要从事白酒生产和销售",
            industry_classification="白酒",
        ),
    )


# ============================================================
# 测试路1：原文搬运工
# ============================================================

class TestPath1Lookup:
    """路1（原文搬运）隔离测试"""

    def test_extract_indicator_from_deviation(self):
        """从 DeviationAnomaly 提取指标名"""
        from layers.layer_d_reasoning.d1_lookup_notes import _extract_indicator_name
        anomaly = _make_deviation_anomaly()
        assert _extract_indicator_name(anomaly) == "存货周转率"

    def test_extract_indicator_from_logic(self):
        """从 LogicAnomaly 提取指标名"""
        from layers.layer_d_reasoning.d1_lookup_notes import _extract_indicator_name
        anomaly = _make_logic_anomaly()
        result = _extract_indicator_name(anomaly)
        assert result == "net_profit_cash_ratio"

    def test_extract_actual_value_deviation(self):
        """从 DeviationAnomaly 提取实际值"""
        from layers.layer_d_reasoning.d1_lookup_notes import _extract_actual_value
        anomaly = _make_deviation_anomaly()
        assert _extract_actual_value(anomaly) == "0.3"

    def test_extract_actual_value_logic(self):
        """从 LogicAnomaly 提取实际值"""
        from layers.layer_d_reasoning.d1_lookup_notes import _extract_actual_value
        anomaly = _make_logic_anomaly()
        assert _extract_actual_value(anomaly) == "0.3"

    def test_format_deviation_with_mad(self):
        """MAD 偏离度格式化"""
        from layers.layer_d_reasoning.d1_lookup_notes import _format_deviation
        anomaly = _make_deviation_anomaly(mad_multiple=3.5)
        result = _format_deviation(anomaly)
        assert "3.5" in result
        assert "MAD" in result
        assert "显著" in result  # 3.5 >= 2.0 → 显著，非极端

    def test_format_deviation_logic(self):
        """LogicAnomaly 偏离度格式化（value/threshold）"""
        from layers.layer_d_reasoning.d1_lookup_notes import _format_deviation
        anomaly = _make_logic_anomaly()
        result = _format_deviation(anomaly)
        assert "0.3" in result
        assert "0.6" in result

    def test_build_md_text(self):
        """管理层讨论文本拼接"""
        from layers.layer_d_reasoning.d1_lookup_notes import _build_md_text
        raw_doc = _make_minimal_raw_doc()
        text = _build_md_text(raw_doc.management_discussion)
        assert "经营情况讨论" in text
        assert "新市场开拓" in text
        assert "第15页" in text
        assert "核心竞争力" in text

    def test_build_footnotes_text(self):
        """附注文本拼接"""
        from layers.layer_d_reasoning.d1_lookup_notes import _build_footnotes_text
        raw_doc = _make_minimal_raw_doc()
        text = _build_footnotes_text(raw_doc.footnotes)
        assert "存货附注" in text
        assert "原材料备货" in text
        assert "第120页" in text

    def test_parse_result_empty(self):
        """空解释列表"""
        from layers.layer_d_reasoning.d1_lookup_notes import _parse_result
        raw = json.dumps({"anomaly_indicator": "存货周转率", "explanations": []})
        result = _parse_result(raw)
        assert result == []

    def test_parse_result_with_explanations(self):
        """正常解析带 confidence_rank 的解释"""
        from layers.layer_d_reasoning.d1_lookup_notes import _parse_result
        raw = json.dumps({
            "anomaly_indicator": "存货周转率",
            "explanations": [
                {
                    "explanation": "存货增加主要因系列酒备货所致",
                    "confidence_rank": 1,
                    "sources": [
                        {"type": "管理层讨论", "location": "第15页", "excerpt": "存货增加主要系加大备货力度所致"}
                    ],
                },
                {
                    "explanation": "原材料价格上涨导致采购成本增加",
                    "confidence_rank": 2,
                    "sources": [
                        {"type": "附注", "location": "第120页", "excerpt": "存货期末余额较上年增加30%"}
                    ],
                },
            ],
        })
        result = _parse_result(raw)
        assert len(result) == 2
        assert result[0].summary == "存货增加主要因系列酒备货所致"
        assert result[0].confidence_rank == 1
        assert result[0].page_number == 15
        assert "备货力度" in result[0].source_text
        assert result[1].confidence_rank == 2
        assert result[1].page_number == 120

    def test_parse_result_sort_by_rank(self):
        """结果应按 confidence_rank 升序排列"""
        from layers.layer_d_reasoning.d1_lookup_notes import _parse_result
        raw = json.dumps({
            "anomaly_indicator": "毛利率",
            "explanations": [
                {
                    "explanation": "原因B",
                    "confidence_rank": 3,
                    "sources": [{"type": "附注", "location": "第50页", "excerpt": "原因B原文"}],
                },
                {
                    "explanation": "原因A",
                    "confidence_rank": 1,
                    "sources": [{"type": "管理层讨论", "location": "第10页", "excerpt": "原因A原文"}],
                },
                {
                    "explanation": "原因C",
                    "confidence_rank": 2,
                    "sources": [{"type": "管理层讨论", "location": "第20页", "excerpt": "原因C原文"}],
                },
            ],
        })
        result = _parse_result(raw)
        assert [e.summary for e in result] == ["原因A", "原因C", "原因B"]

    def test_extract_page_number(self):
        """页码提取"""
        from layers.layer_d_reasoning.d1_lookup_notes import _extract_page_number
        assert _extract_page_number("第15页") == 15
        assert _extract_page_number("第120-121页") == 120
        assert _extract_page_number("15") == 15
        assert _extract_page_number("") == 0
        assert _extract_page_number(None) == 0


# ============================================================
# 测试路2：外部情报推演
# ============================================================

class TestPath2Hypothesis:
    """路2（外部推演）隔离测试"""

    def test_extract_indicator_name(self):
        """指标名提取"""
        from layers.layer_d_reasoning.d1_hypothesis import _extract_indicator_name
        anomaly = _make_deviation_anomaly()
        assert _extract_indicator_name(anomaly) == "存货周转率"

    def test_format_deviation(self):
        """偏离度格式化"""
        from layers.layer_d_reasoning.d1_hypothesis import _format_deviation
        anomaly = _make_deviation_anomaly(mad_multiple=3.5)
        result = _format_deviation(anomaly)
        assert "3.5" in result
        assert "显著" in result  # 3.5 >= 2.0 → 显著，非极端
        assert "实际值" in result

    def test_format_industry_tags(self):
        """行业标签格式化"""
        from layers.layer_d_reasoning.d1_hypothesis import _format_industry_tags
        tags = CompanyTags(
            company_name="茅台",
            stock_code="600519",
            hard_tags=[
                HardTag(system="同花顺三级行业", value="白酒"),
                HardTag(system="同花顺二级行业", value="饮料制造"),
            ],
        )
        result = _format_industry_tags(tags)
        assert "白酒" in result
        assert "饮料制造" in result

    def test_format_industry_tags_empty(self):
        """空标签时返回默认提示"""
        from layers.layer_d_reasoning.d1_hypothesis import _format_industry_tags
        result = _format_industry_tags(None)
        assert "无行业标签" in result

    def test_parse_result_with_sources(self):
        """解析带多条 source 的假设"""
        from layers.layer_d_reasoning.d1_hypothesis import _parse_result
        raw = json.dumps({
            "anomaly_indicator": "存货周转率",
            "explanations": [
                {
                    "explanation": "行业需求疲软导致库存积压",
                    "confidence_rank": 1,
                    "sources": [
                        {"type": "券商深度报告", "name": "中信证券", "detail": "白酒行业2024年需求下降15%"},
                        {"type": "行业新闻", "name": "酒业时报", "detail": "渠道库存高企"},
                    ],
                },
                {
                    "explanation": "公司战略备货",
                    "confidence_rank": 2,
                    "sources": [
                        {"type": "A0宏观事实", "name": "", "detail": "原材料价格预期上涨"},
                    ],
                },
            ],
        })
        result = _parse_result(raw)
        assert len(result) == 2
        # 第一条假设
        assert result[0].hypothesis == "行业需求疲软导致库存积压"
        assert result[0].confidence_rank == 1
        assert "中信证券" in result[0].reasoning
        assert "白酒行业2024年需求下降15%" in result[0].reasoning
        assert "券商深度报告" in result[0].source
        # 第二条假设
        assert result[1].hypothesis == "公司战略备货"
        assert result[1].confidence_rank == 2

    def test_parse_result_sort_by_rank(self):
        """结果应按 confidence_rank 升序排列"""
        from layers.layer_d_reasoning.d1_hypothesis import _parse_result
        raw = json.dumps({
            "anomaly_indicator": "毛利率",
            "explanations": [
                {
                    "explanation": "假设C",
                    "confidence_rank": 3,
                    "sources": [{"type": "行业新闻", "name": "", "detail": ""}],
                },
                {
                    "explanation": "假设A",
                    "confidence_rank": 1,
                    "sources": [{"type": "券商深度报告", "name": "", "detail": ""}],
                },
            ],
        })
        result = _parse_result(raw)
        assert [h.hypothesis for h in result] == ["假设A", "假设C"]

    def test_parse_result_empty(self):
        """空假设列表"""
        from layers.layer_d_reasoning.d1_hypothesis import _parse_result
        raw = json.dumps({"anomaly_indicator": "存货周转率", "explanations": []})
        result = _parse_result(raw)
        assert result == []

    def test_no_template_fallback(self):
        """验证路2没有模板降级——不应存在 _template_hypotheses"""
        import layers.layer_d_reasoning.d1_hypothesis as m
        assert not hasattr(m, "_template_hypotheses")

    def test_no_financial_profile_in_function(self):
        """验证路2函数签名不包含 financial_profile"""
        import inspect
        from layers.layer_d_reasoning.d1_hypothesis import generate_hypotheses
        sig = inspect.signature(generate_hypotheses)
        params = list(sig.parameters.keys())
        # 不应有 financial_profile 或 tags.financial_profile
        assert "financial_profile" not in params


# ============================================================
# 测试编排层
# ============================================================

class TestDualPath:
    """D1 编排层测试"""

    def test_business_desc_extracted(self):
        """编排层应从 raw_doc 提取 business_desc ——验证函数签名"""
        from layers.layer_d_reasoning.d1_dual_path import run_dual_path_analysis
        from layers.layer_d_reasoning.d1_hypothesis import generate_hypotheses
        import inspect

        # 验证 generate_hypotheses 签名包含 business_desc 参数
        sig = inspect.signature(generate_hypotheses)
        assert "business_desc" in sig.parameters

        # 验证 run_dual_path_analysis 内部提取 business_desc
        #（通过检查源代码确认）
        source = inspect.getsource(run_dual_path_analysis)
        assert "business_description" in source
        assert "company_overview" in source

    def test_path_does_not_catch_exceptions(self):
        """路1 和路2 都不应捕获 LLM 异常（无 try/except 包裹）"""
        import inspect
        from layers.layer_d_reasoning import d1_lookup_notes, d1_hypothesis

        for mod, name in [(d1_lookup_notes, "lookup_in_annual_report"),
                          (d1_hypothesis, "generate_hypotheses")]:
            source = inspect.getsource(getattr(mod, name))
            assert "try:" not in source, f"{name} 不应包含 try/except"


# ============================================================
# 测试 Schema 兼容性
# ============================================================

class TestSchemaCompatibility:
    """D1 输出与 D2 兼容性测试"""

    def test_explanation_has_confidence_rank(self):
        """Explanation 新字段 confidence_rank"""
        exp = Explanation(
            summary="存货增加",
            source_text="存货增加主要系备货所致",
            page_number=15,
            confidence_rank=2,
        )
        assert exp.confidence_rank == 2
        assert exp.summary == "存货增加"
        assert exp.is_vague is False

    def test_hypothesis_has_confidence_rank(self):
        """Hypothesis 新字段 confidence_rank"""
        hyp = Hypothesis(
            hypothesis="战略备货",
            reasoning="基于行业数据",
            source="券商报告",
            confidence_rank=1,
        )
        assert hyp.confidence_rank == 1
        assert hyp.hypothesis == "战略备货"

    def test_d2_compatible(self):
        """D1 输出仍可被 D2 消费（字段风格兼容）"""
        from schemas.reasoning import ProbabilityAssignment
        explanation = Explanation(
            summary="存货增加",
            source_text="存货增加主要系备货所致",
            page_number=15,
            confidence_rank=1,
        )
        hypothesis = Hypothesis(
            hypothesis="战略备货",
            reasoning="行业需求",
            source="券商报告",
            confidence_rank=1,
        )
        # ProbabilityAssignment 仍可接收 Explanation 和 Hypothesis
        assignment = ProbabilityAssignment(
            anomaly_indicator="存货周转率",
            anomaly_source="C",
            lookups=[explanation],
            hypotheses=[hypothesis],
            probabilities={"战略备货": 0.6, "其他": 0.4},
        )
        assert len(assignment.lookups) == 1
        assert len(assignment.hypotheses) == 1
