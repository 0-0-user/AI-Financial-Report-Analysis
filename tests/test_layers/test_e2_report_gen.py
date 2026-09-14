"""
==========================================================
 tests/test_layers/test_e2_report_gen.py — 归因完整度 -> 置信度分级
==========================================================

背景:
    分数高只说明"没扣到分"，不等于"结论确定"。当异常归因大量落进
    「其他原因」（D-S 的未分配质量 Θ）时，系统其实没能解释这个异常。
    旧实现下 confidence_tier 只看分数，于是"检出 abnormal 异常 + 100 分
    + 高置信度"三者同时出现 —— 一个 fail-open 的风控结论。
"""

import pytest

from pipeline.context import PipelineContext
from schemas.benchmark import Benchmark, IndustryProfile, MIN_PEER_SAMPLE
from schemas.reasoning import Explanation, Hypothesis, ProbabilityAssignment
from schemas.report import (
    AnomalyScoreDetail,
    OverallAssessment,
    PeerComparison,
    Report,
    ScoreBreakdown,
)
from layers.layer_e_output.e1_scoring import OTHER_CAUSE
from layers.layer_e_output.e2_report_gen import (
    M_THETA_LOW_TIER,
    M_THETA_MEDIUM_TIER,
    _build_evidence_sources,
    _build_explanation_data,
    _build_overview,
    _build_trust_interval,
    _render_business_overview_md,
    _render_explanation_fallback,
    _render_summary_fallback,
    render_to_markdown,
)


def _detail(m_theta: float, bel: float, pl: float, center: float) -> AnomalyScoreDetail:
    """手工构造一条 E1 评分明细。

    不变式 pl <= bel <= 0：E1 的 _clamp_non_positive 保证异常对总分的贡献
    上限为 0（检出异常不能倒加分），故**正的 pl 已是不可能输入**。
    早先这里用 pl=+4.0 构造，于是 test_interval_spans_bel_and_pl 把
    upper_bound == 104.0 钉成了正确值 —— 正是 H1 要消掉的那个行为。
    """
    return AnomalyScoreDetail(
        indicator="毛利率异常", source="B+",
        w_phe=2.0, k_value=0.0, delta=1.0,
        causes=[],                 # 「其他原因」不是一条归因，不进 causes
        anomaly_score=center,
        score_bel=bel, score_pl=pl,
        m_theta=m_theta,
    )


def _ctx(details: list, final_score: float) -> PipelineContext:
    """默认带一个样本充足的同行基准。

    没有可用基准时，满分只说明"横向偏离没算出来"，E2 会挂告示并压低
    置信度（见 TestInsufficientBenchmarkCaveat）—— 那些用例自己构造 ctx。
    这里给足样本，是为了让"归因完整就该是高置信度"这类断言只考验归因逻辑。
    """
    ctx = PipelineContext()
    ctx.score = ScoreBreakdown(
        base_score=100.0,
        anomaly_details=details,
        total_deduction=round(final_score - 100.0, 2),
        final_score=final_score,
    )
    ctx.benchmark = Benchmark(
        industry=IndustryProfile(industry_name="测试行业"),
        peer_median={"XSMLL": 30.0}, historical_mean={}, peer_companies=[],
        peer_sample_size=MIN_PEER_SAMPLE, sample_sufficient=True,
    )
    return ctx


class TestTrustIntervalAggregation:
    def test_m_theta_comes_from_e1_not_back_derived(self):
        """avg_m_theta 直接取 E1 给的值（旧实现靠 bel/pl 差值反推，公式本身有误）"""
        ctx = _ctx([_detail(1.0, -6.0, 0.0, 0.0)], 100.0)
        ti = _build_trust_interval(ctx)
        assert ti.avg_m_theta == pytest.approx(1.0)

    def test_interval_spans_bel_and_pl(self):
        """苏美达形态: 100% 未归因 -> 区间 [94, 100]，不塌成一点

        上界是满分，不是 104：乐观端 pl 被 E1 钳在 0（异常不能倒加分）。
        区间仍不塌成一点 —— 不确定性的表达全部落在悲观端。
        """
        ctx = _ctx([_detail(1.0, -6.0, 0.0, 0.0)], 100.0)
        ti = _build_trust_interval(ctx)
        assert ti.center_score == 100.0
        assert ti.lower_bound == 94.0
        assert ti.upper_bound == 100.0

    def test_no_anomalies_is_a_point_interval(self):
        ctx = _ctx([], 100.0)
        ti = _build_trust_interval(ctx)
        assert ti.avg_m_theta == 0.0
        assert (ti.lower_bound, ti.upper_bound) == (100.0, 100.0)


class TestConfidenceTier:
    def test_unattributed_anomaly_downgrades_high_score_to_low(self):
        """核心回归: 100 分 + 完全无法归因 -> 低置信度，而非高置信度"""
        ctx = _ctx([_detail(1.0, -6.0, 0.0, 0.0)], 100.0)
        ti = _build_trust_interval(ctx)
        assert _build_overview(ctx, ti).confidence_tier == "低置信度"

    def test_partial_unknown_downgrades_to_medium(self):
        ctx = _ctx([_detail(M_THETA_MEDIUM_TIER, -5.0, 0.0, -2.0)], 98.0)
        ti = _build_trust_interval(ctx)
        assert _build_overview(ctx, ti).confidence_tier == "中等置信度"

    def test_fully_attributed_keeps_high_tier(self):
        """归因完整时不得误伤: 高分仍是高置信度"""
        ctx = _ctx([_detail(0.0, -1.0, -1.0, -1.0)], 99.0)
        ti = _build_trust_interval(ctx)
        assert _build_overview(ctx, ti).confidence_tier == "高置信度"

    def test_no_anomalies_keeps_high_tier(self):
        ctx = _ctx([], 100.0)
        ti = _build_trust_interval(ctx)
        assert _build_overview(ctx, ti).confidence_tier == "高置信度"

    def test_correction_never_upgrades_tier(self):
        """校正只能降级，不能提级"""
        ctx = _ctx([_detail(0.0, -50.0, -50.0, -50.0)], 50.0)
        ti = _build_trust_interval(ctx)
        assert _build_overview(ctx, ti).confidence_tier == "低置信度"

    def test_thresholds_are_ordered(self):
        assert 0 < M_THETA_MEDIUM_TIER < M_THETA_LOW_TIER < 1


class TestExplanationData:
    """解释数据构建: 「其他原因」不是归因，也不能被挂上假证据"""

    @staticmethod
    def _assignment(probabilities: dict) -> ProbabilityAssignment:
        return ProbabilityAssignment(
            anomaly_indicator="毛利率异常",
            anomaly_source="B+",
            lookups=[], hypotheses=[],
            probabilities=probabilities,
            ds_metadata={"conflict_K": 0.0},
            semantic_scores={}, semantic_keywords={},
        )

    def test_other_cause_becomes_unattributed_not_a_cause(self):
        ctx = PipelineContext()
        ctx.reasoning_results = [self._assignment({"其他原因": 1.0})]

        data = _build_explanation_data(ctx)

        assert len(data) == 1
        assert data[0]["causes"] == []          # 不是一条归因
        assert data[0]["unattributed"] == 100.0  # 而是"未能归因 100%"

    def test_known_causes_kept_and_unknown_measured(self):
        ctx = PipelineContext()
        ctx.reasoning_results = [self._assignment({"行业需求下降": 0.7, "其他原因": 0.3})]

        data = _build_explanation_data(ctx)

        assert [c["cause"] for c in data[0]["causes"]] == ["行业需求下降"]
        assert data[0]["causes"][0]["probability"] == 70.0
        assert data[0]["unattributed"] == 30.0

    def test_fallback_renders_unattributed_explicitly(self):
        ctx = PipelineContext()
        ctx.reasoning_results = [self._assignment({"其他原因": 1.0})]

        text = _render_explanation_fallback(_build_explanation_data(ctx))

        assert "未能归因" in text
        # 不得作为一条"原因"列出（原因条目的格式是 "- {原因}: {概率}%"）
        assert "- 其他原因:" not in text

    def test_fallback_omits_unattributed_line_when_fully_attributed(self):
        ctx = PipelineContext()
        ctx.reasoning_results = [self._assignment({"行业需求下降": 1.0})]

        text = _render_explanation_fallback(_build_explanation_data(ctx))

        assert "行业需求下降" in text
        assert "未能归因" not in text


class TestPeerComparisonRendering:
    """回归: Markdown 渲染曾整篇失败 —— 'PeerComparison' object has no attribute 'name'。

    `_render_business_overview_md` 里拿同行公司名时写的是 `p.name`，但
    这里遍历的是 `PeerComparison`（字段 `company_name`）；`name` 是 A 层
    `benchmark.peer_companies` 的字段。异常被上层吞成"非阻断 WARNING"，
    结果 .md 报告根本没生成，而调用方仍报成功。
    """

    @staticmethod
    def _report(**kw) -> Report:
        base = dict(
            company_name="苏美达", stock_code="600710", report_year=2025,
            overall_assessment=OverallAssessment(
                score=93.55, confidence_tier="低置信度", peer_comparisons=[],
            ),
            core_anomalies=[],
        )
        base.update(kw)
        return Report(**base)

    def test_renders_with_peer_comparisons(self):
        report = self._report(
            overall_assessment=OverallAssessment(
                score=93.55, confidence_tier="低置信度",
                peer_comparisons=[
                    PeerComparison(company_name="苏美达", similarity_score=0.0),
                    PeerComparison(company_name="厦门国贸", similarity_score=0.87),
                ],
            ),
        )
        text = _render_business_overview_md(report)

        assert "苏美达" in text
        assert "厦门国贸" in text

    def test_renders_without_peers(self):
        """peers 为空时不得因这一块出错"""
        text = _render_business_overview_md(self._report())
        assert "可比行业组" not in text


# ============================================================
# 证据溯源 —— 归因名 vs D1 原文出处
# ============================================================

class TestEvidenceSources:
    """回归: 证据溯源曾 29/29 全部退化成系统自造的「综合分析」。

    根因: probabilities 的 key 是 D2 让 LLM 语义合并时【重新起】的统一
    归因名 (见 d2_probability._llm_merge_conflict)，与 D1 的
    Explanation.summary / Hypothesis.hypothesis 不是同一套字符串。
    _build_evidence_sources 却拿归因名去做子串匹配回找原文，两套命名
    体系对不上，必然全部落空 -> 可核验率恒为 0%。
    正确做法是用 D2 给出的下标映射 path1_indices / path2_indices。
    """

    # 故意起一个和 D1 原文完全无子串关系的归纳名
    MERGED = "以旧换新政策下高周转低毛利品类占比上升"

    @staticmethod
    def _assignment(name, lookups, hypotheses, merged, extra_keys=()):
        probs = {name: 0.6}
        for k in extra_keys:
            probs[k] = 0.2
        return ProbabilityAssignment(
            anomaly_indicator="毛利率异常",
            anomaly_source="B+",
            lookups=lookups,
            hypotheses=hypotheses,
            probabilities=probs,
            ds_metadata={"conflict_K": 0.0, "merged_causes": merged},
        )

    @staticmethod
    def _ctx(*assignments):
        ctx = PipelineContext()
        ctx.reasoning_results = list(assignments)
        return ctx

    def test_renamed_cause_resolves_by_index(self):
        """核心回归: 名称对不上，按下标仍能拿到原文 + 页码"""
        lk = Explanation(
            summary="毛利率下滑原因", source_text="受以旧换新政策影响……",
            page_number=42,
        )
        # 前提: 名称确实对不上，旧实现在这里必然落空
        assert lk.summary not in self.MERGED

        ctx = self._ctx(self._assignment(
            self.MERGED, [lk], [],
            [{"name": self.MERGED, "path1_indices": [0], "path2_indices": [],
              "conflicts_with": []}],
        ))
        sources = _build_evidence_sources(ctx)

        assert len(sources) == 1
        assert sources[0].cause == self.MERGED
        assert sources[0].source_type == "年报原文"
        assert sources[0].source_text == "受以旧换新政策影响……"
        assert sources[0].page_number == 42
        assert sources[0].footnote_id == "fn_001"

    def test_falls_back_to_hypothesis_when_no_lookup_index(self):
        """路1 无下标 -> 走路2 假设，并带上推演逻辑"""
        h = Hypothesis(
            hypothesis="渠道压货导致价格倒挂", reasoning="经销商库存周转天数同比翻倍",
            source="行业新闻", confidence_rank=1,
        )
        ctx = self._ctx(self._assignment(
            self.MERGED, [], [h],
            [{"name": self.MERGED, "path1_indices": [], "path2_indices": [0],
              "conflicts_with": []}],
        ))
        sources = _build_evidence_sources(ctx)

        assert len(sources) == 1
        assert sources[0].source_type == "推演假设"
        assert sources[0].source_text == "经销商库存周转天数同比翻倍"
        assert sources[0].hypothesis_reasoning == "经销商库存周转天数同比翻倍"

    def test_lookup_wins_over_hypothesis(self):
        """两路都有下标 -> 路1 原文优先（原文可核验，假设不可）"""
        lk = Explanation(summary="摘要", source_text="年报原话", page_number=7)
        h = Hypothesis(hypothesis="假设", reasoning="推演逻辑", source="宏观事实")
        ctx = self._ctx(self._assignment(
            self.MERGED, [lk], [h],
            [{"name": self.MERGED, "path1_indices": [0], "path2_indices": [0],
              "conflicts_with": []}],
        ))
        sources = _build_evidence_sources(ctx)
        assert sources[0].source_type == "年报原文"
        assert sources[0].source_text == "年报原话"
        assert sources[0].page_number == 7

    def test_out_of_range_index_degrades_to_comprehensive(self):
        """下标越界: 宁可记「综合分析」，也不挂一条指错地方的引用"""
        lk = Explanation(summary="摘要", source_text="原话", page_number=7)
        ctx = self._ctx(self._assignment(
            self.MERGED, [lk], [],
            [{"name": self.MERGED, "path1_indices": [5], "path2_indices": [],
              "conflicts_with": []}],
        ))
        sources = _build_evidence_sources(ctx)
        assert sources[0].source_type == "综合分析"
        assert sources[0].page_number is None

    def test_bool_index_is_not_treated_as_int(self):
        """bool 是 int 子类，True 会被当成长度 1 —— 必须显式排除"""
        lk = Explanation(summary="摘要", source_text="原话", page_number=7)
        ctx = self._ctx(self._assignment(
            self.MERGED, [lk], [],
            [{"name": self.MERGED, "path1_indices": [True], "path2_indices": [],
              "conflicts_with": []}],
        ))
        sources = _build_evidence_sources(ctx)
        assert sources[0].source_type == "综合分析"

    def test_other_cause_is_not_an_evidence_entry(self):
        """「其他原因」是 D-S 未分配质量 Θ 的记名，不是一条归因"""
        lk = Explanation(summary="摘要", source_text="原话", page_number=7)
        ctx = self._ctx(self._assignment(
            self.MERGED, [lk], [],
            [{"name": self.MERGED, "path1_indices": [0], "path2_indices": [],
              "conflicts_with": []}],
            extra_keys=(OTHER_CAUSE,),
        ))
        sources = _build_evidence_sources(ctx)

        assert [s.cause for s in sources] == [self.MERGED]
        assert len(sources) == 1

    def test_legacy_data_without_merged_causes(self):
        """旧数据没有 merged_causes: 不崩，名称能对上就仍溯源，对不上记综合分析"""
        hit = Explanation(summary="存货增加", source_text="加大备货", page_number=15)
        miss = Explanation(summary="毫不相干的摘要", source_text="另一段", page_number=16)
        ctx = PipelineContext()
        ctx.reasoning_results = [
            ProbabilityAssignment(
                anomaly_indicator="存货周转率", anomaly_source="C",
                lookups=[hit, miss], hypotheses=[],
                probabilities={"存货增加": 0.7, "别的原因": 0.3},
                ds_metadata=None,          # 旧数据没有 ds_metadata
            ),
        ]
        sources = _build_evidence_sources(ctx)

        by_cause = {s.cause: s for s in sources}
        assert by_cause["存货增加"].source_type == "年报原文"
        assert by_cause["存货增加"].page_number == 15
        assert by_cause["别的原因"].source_type == "综合分析"

    def test_no_reasoning_results(self):
        assert _build_evidence_sources(PipelineContext()) == []

    def test_footnote_ids_are_sequential_across_anomalies(self):
        """脚注编号跨异常连续，不能每个异常从 fn_001 重新开始"""
        lk = Explanation(summary="s", source_text="t", page_number=1)
        merged = [{"name": "A", "path1_indices": [0], "path2_indices": [],
                   "conflicts_with": []}]
        a1 = self._assignment("A", [lk], [], merged)
        a2 = self._assignment("A", [lk], [], merged)
        sources = _build_evidence_sources(self._ctx(a1, a2))
        assert [s.footnote_id for s in sources] == ["fn_001", "fn_002"]


# ============================================================
# 基准样本退化必须显式告示
# ============================================================

class TestInsufficientBenchmarkCaveat:
    """回归: 样本退化会被读成"公司各项指标都正常"。

    C 层拒绝做偏离判定时，异常数 0、扣分 0 -> 满分。
    若不显式告示，报告呈现的就是"100 分 + 高置信度"，
    和"真的横向比过、没问题"长得一模一样 —— 同一个 fail-open 家族。
    """

    @staticmethod
    def _bench_ctx(sufficient: bool, n: int, final_score: float = 100.0):
        ctx = PipelineContext()
        ctx.score = ScoreBreakdown(
            base_score=100.0, anomaly_details=[],
            total_deduction=0.0, final_score=final_score,
        )
        ctx.benchmark = Benchmark(
            industry=IndustryProfile(industry_name="贸易Ⅱ"),
            peer_median={}, historical_mean={}, peer_companies=[],
            peer_sample_size=n, sample_sufficient=sufficient,
        )
        return ctx

    def test_insufficient_sample_downgrades_tier_and_warns(self):
        ctx = self._bench_ctx(sufficient=False, n=1)
        ti = _build_trust_interval(ctx)
        overview = _build_overview(ctx, ti)

        assert overview.confidence_tier == "中等置信度"   # 从"高"压下来
        assert overview.caveats, "样本不足却没给任何告示"
        assert "同行样本不足" in overview.caveats[0]
        assert "1 家" in overview.caveats[0]

    def test_no_benchmark_at_all_also_warns(self):
        ctx = PipelineContext()
        ctx.score = ScoreBreakdown(
            base_score=100.0, anomaly_details=[],
            total_deduction=0.0, final_score=100.0,
        )
        overview = _build_overview(ctx, _build_trust_interval(ctx))

        assert overview.caveats
        assert overview.confidence_tier == "中等置信度"

    def test_sufficient_sample_has_no_caveat(self):
        """修复不得误伤: 样本充足时不该冒出告示、也不该压级"""
        ctx = self._bench_ctx(sufficient=True, n=MIN_PEER_SAMPLE)
        overview = _build_overview(ctx, _build_trust_interval(ctx))

        assert overview.caveats == []
        assert overview.confidence_tier == "高置信度"

    def test_caveat_is_rendered_into_markdown(self):
        """告示必须真的出现在报告里，不能只躺在数据结构里"""
        ctx = self._bench_ctx(sufficient=False, n=2)
        ti = _build_trust_interval(ctx)
        report = Report(
            company_name="苏美达", stock_code="600710", report_year=2025,
            overall_assessment=_build_overview(ctx, ti),
            core_anomalies=[],
        )

        overview_md = _render_business_overview_md(report, ctx)
        assert "同行样本不足" in overview_md

        full_md = render_to_markdown(report, ctx)
        assert "同行样本不足" in full_md

    def test_summary_fallback_carries_caveat(self):
        ctx = self._bench_ctx(sufficient=False, n=0)
        overview = _build_overview(ctx, _build_trust_interval(ctx))

        text = _render_summary_fallback(ctx.score, overview, None, [])

        assert "同行样本不足" in text
