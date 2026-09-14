"""
==========================================================
 tests/test_layers/test_e1_scoring.py — 新评分公式测试
==========================================================

测试 E1 层新公式:
    Score_j = W_phe x (1 - K) x Σ P x S_base
    Score_j <= 0              (异常只能扣分或中性，见 e1_scoring._clamp_non_positive)
    Final = 100 + Σ Score_j   (下界不钳：允许负数)

测试方式: 构造 PipelineContext 注入 mock 数据，验证各项中间值。
"""

import pytest
from pipeline.context import PipelineContext
from schemas.anomaly import DeviationAnomaly, LogicAnomaly
from schemas.reasoning import ProbabilityAssignment, Explanation, Hypothesis
from schemas.report import ScoreBreakdown, AnomalyScoreDetail
from layers.layer_e_output.e1_scoring import run_scoring, _calc_c_w_phe


class TestScoring:
    """新公式的置信度打分测试"""

    def test_base_score_no_anomalies(self):
        """无异常时应保持 100 分"""
        ctx = PipelineContext()
        result = run_scoring(ctx)

        assert isinstance(result, ScoreBreakdown)
        assert result.base_score == 100
        assert result.total_deduction == 0
        assert result.final_score == 100
        assert result.anomaly_details == []

    def test_deduction_with_c_layer_deviation(self):
        """C 层偏差异常 + D层推理 + D3语义匹配 -> 正确扣分"""
        ctx = PipelineContext()
        ctx.deviations = [
            DeviationAnomaly(
                indicator="存货周转率",
                actual_value=0.1,
                benchmark_value=0.5,
                mad_multiple=4.0,  # W_phe = min(4/3, 5) = 1.33
                severity="extreme",
            ),
        ]
        ctx.reasoning_results = [
            ProbabilityAssignment(
                anomaly_indicator="存货周转率",
                anomaly_source="C",
                lookups=[],
                hypotheses=[],
                probabilities={"行业需求下降导致销售放缓": 0.6, "产品滞销导致库存积压": 0.4},
                ds_metadata={"conflict_K": 0.2},
                semantic_scores={"行业需求下降导致销售放缓": -1, "产品滞销导致库存积压": -2},
                semantic_keywords={"行业需求下降导致销售放缓": ["下滑"], "产品滞销导致库存积压": ["异常", "风险"]},
            ),
        ]
        result = run_scoring(ctx)

        # 验证计算结果
        assert len(result.anomaly_details) == 1
        detail = result.anomaly_details[0]

        # W_phe = min(4/3, 5) ~ 1.3333
        assert detail.w_phe == pytest.approx(1.3333, rel=1e-3)
        # K = 0.2
        assert detail.k_value == 0.2
        assert detail.delta == 0.8

        # weighted_sum = 0.6 x (-1) + 0.4 x (-2) = -0.6 - 0.8 = -1.4
        # effective = -1.4 x 0.8 = -1.12
        # anomaly_score = 1.3333 x (-1.12) ~ -1.4933 -> round到2位 = -1.49
        assert detail.anomaly_score == pytest.approx(-1.49, rel=1e-2)

        assert result.total_deduction == pytest.approx(-1.49, rel=1e-2)
        assert result.final_score == pytest.approx(98.51, rel=1e-2)

    def test_bplus_anomaly_deduction(self):
        """B+ 层逻辑异常 + D层推理 -> 正确扣分"""
        ctx = PipelineContext()
        ctx.logic_anomalies = [
            LogicAnomaly(
                check_name="利润含金量异常",
                value=0.3,
                threshold=0.6,
                severity=3.0,  # 直接用做 W_phe
                summary="净现比低",
            ),
        ]
        ctx.reasoning_results = [
            ProbabilityAssignment(
                anomaly_indicator="利润含金量异常",
                anomaly_source="B+",
                lookups=[],
                hypotheses=[],
                probabilities={"行业竞争加剧导致回款困难": 0.7, "企业放宽信用政策虚增收入": 0.3},
                ds_metadata={"conflict_K": 0.35},
                semantic_scores={"行业竞争加剧导致回款困难": 0, "企业放宽信用政策虚增收入": -3},
                semantic_keywords={"行业竞争加剧导致回款困难": ["行业性质"], "企业放宽信用政策虚增收入": ["虚增"]},
            ),
        ]
        result = run_scoring(ctx)

        assert len(result.anomaly_details) == 1
        detail = result.anomaly_details[0]

        assert detail.source == "B+"
        assert detail.w_phe == 3.0
        assert detail.k_value == 0.35
        assert detail.delta == 0.65

        # weighted_sum = 0.7 x 0 + 0.3 x (-3) = -0.9
        # effective = -0.9 x 0.65 = -0.585
        # anomaly_score = 3.0 x (-0.585) = -1.755
        assert detail.anomaly_score == pytest.approx(-1.755, rel=1e-3)
        assert result.final_score == pytest.approx(98.245, rel=1e-3)

    def test_k_zero_equals_no_conflict(self):
        """K=0 时 δ=1，全额生效"""
        ctx = PipelineContext()
        ctx.deviations = [
            DeviationAnomaly(
                indicator="毛利率", actual_value=0.2, benchmark_value=0.5,
                mad_multiple=3.0, severity="abnormal",
            ),
        ]
        ctx.reasoning_results = [
            ProbabilityAssignment(
                anomaly_indicator="毛利率",
                anomaly_source="C",
                lookups=[], hypotheses=[],
                probabilities={"成本上升压缩利润空间": 1.0},
                ds_metadata={"conflict_K": 0.0},
                semantic_scores={"成本上升压缩利润空间": -1},
                semantic_keywords={},
            ),
        ]
        result = run_scoring(ctx)

        detail = result.anomaly_details[0]
        assert detail.delta == 1.0  # 不打折
        # W_phe = min(3/3, 5) = 1.0
        assert detail.w_phe == 1.0
        # anomaly_score = 1.0 x 1.0 x (1.0 x -1) = -1.0
        assert detail.anomaly_score == -1.0

    def test_negative_score_allowed(self):
        """允许负分，不 clamp"""
        ctx = PipelineContext()
        ctx.logic_anomalies = [
            LogicAnomaly(
                check_name="财务造假",
                value=999.0, threshold=1.0,
                severity=5.0,  # 最高严重度
                summary="系统性财务造假",
            ),
        ]
        ctx.reasoning_results = [
            ProbabilityAssignment(
                anomaly_indicator="财务造假",
                anomaly_source="B+",
                lookups=[], hypotheses=[],
                probabilities={"系统性财务造假虚增收入": 1.0},
                ds_metadata={"conflict_K": 0.0},
                semantic_scores={"系统性财务造假虚增收入": -3},
                semantic_keywords={"系统性财务造假虚增收入": ["造假"]},
            ),
        ]
        result = run_scoring(ctx)

        # anomaly_score = 5.0 x 1.0 x (1.0 x -3) = -15.0
        assert result.total_deduction == -15.0
        assert result.final_score == 85.0  # 100 - 15 = 85

        # 极端: 7 个同样的极端异常
        ctx2 = PipelineContext()
        ctx2.logic_anomalies = [LogicAnomaly(
            check_name=f"异常{i}", value=1.0, threshold=0.5,
            severity=5.0, summary=f"异常{i}",
        ) for i in range(7)]
        ctx2.reasoning_results = [
            ProbabilityAssignment(
                anomaly_indicator=f"异常{i}",
                anomaly_source="B+",
                lookups=[], hypotheses=[],
                probabilities={"严重问题": 1.0},
                ds_metadata={"conflict_K": 0.0},
                semantic_scores={"严重问题": -3},
                semantic_keywords={},
            ) for i in range(7)
        ]
        result2 = run_scoring(ctx2)

        # 7 x (-15) = -105, final = 100 - 105 = -5
        assert result2.final_score == -5.0  # 允许负分

    def test_w_phe_c_mapping(self):
        """C 层 W_phe 映射公式验证"""
        assert _calc_c_w_phe(2.0) == pytest.approx(0.6667, rel=1e-3)  # 轻微
        assert _calc_c_w_phe(5.0) == pytest.approx(1.6667, rel=1e-3)  # 一般
        assert _calc_c_w_phe(10.0) == pytest.approx(3.3333, rel=1e-3)  # 严重
        assert _calc_c_w_phe(15.0) == 5.0  # cap
        assert _calc_c_w_phe(100.0) == 5.0  # 极端值 cap

    def test_no_reasoning_match_returns_none(self):
        """异常无匹配的 D 层推理结果时，跳过该异常"""
        ctx = PipelineContext()
        ctx.deviations = [
            DeviationAnomaly(
                indicator="存货周转率", actual_value=0.1, benchmark_value=0.5,
                mad_multiple=4.0, severity="extreme",
            ),
        ]
        # 没有注入 reasoning_results
        result = run_scoring(ctx)
        assert result.anomaly_details == []  # 无匹配，跳过
        assert result.final_score == 100.0  # 保留基础分


class TestAnomalyNeverRaisesScore:
    """异常只能扣分或中性，不能加分。

    2026-09-11 真实运行暴露：苏美达 3 条异常(2 条 extreme)合计只扣 0.31，
    因为 Σ P·S_base 是**带符号**加权和 —— 归因偏良性的异常算出正分，
    把真扣分的那条抵掉了(资产负债率 -1.25 被 +0.35 与 +0.59 抹平)。
    semantic_scoring.yaml 里 +1/+2 两档的本意是「良性归因 -> 少扣分」，
    不是「倒加分」；检出一个异常却让总分高于 100，语义上说不通。
    """

    @staticmethod
    def _run(probabilities: dict, semantic_scores: dict) -> ScoreBreakdown:
        """severity=5.0 -> W_phe=5.0，与真实「净资产收益率」那条同量级；K=0 -> delta=1"""
        ctx = PipelineContext()
        ctx.logic_anomalies = [
            LogicAnomaly(
                check_name="净资产收益率", value=0.3, threshold=0.15,
                severity=5.0, summary="ROE 极端偏离同行",
            ),
        ]
        ctx.reasoning_results = [
            ProbabilityAssignment(
                anomaly_indicator="净资产收益率", anomaly_source="B+",
                lookups=[], hypotheses=[],
                probabilities=probabilities,
                ds_metadata={"conflict_K": 0.0},
                semantic_scores=semantic_scores,
                semantic_keywords={},
            ),
        ]
        return run_scoring(ctx)

    def test_benign_explanation_neutralises_but_never_bonuses(self):
        result = self._run(
            probabilities={
                "多元化高景气业务拉动": 0.35,
                "大宗商品补库存推升周转": 0.35,
                "贸易行业盈利分化偏离": 0.30,
            },
            semantic_scores={
                "多元化高景气业务拉动": 1,
                "大宗商品补库存推升周转": 1,
                "贸易行业盈利分化偏离": -1,
            },
        )

        # 手算(照 e1_scoring.py 模块 docstring 的公式，不照实现):
        #   weighted_sum = 0.35x(+1) + 0.35x(+1) + 0.30x(-1) = +0.40
        #   delta = 1 - K = 1.0
        #   未钳位时 score_j = 5.0 x 1.0 x 0.40 = +2.0  <- 这就是 bug
        assert result.anomaly_details[0].anomaly_score == 0.0
        assert result.total_deduction == 0.0
        assert result.final_score == 100.0

    def test_optimistic_interval_end_never_exceeds_full_marks(self):
        """区间乐观端同样钳位，否则信任区间上界会超过满分。

        实测苏美达 2026-09-11: Σ score_pl = +1.1987+0.3206+3.5945 = +5.1138，
        上报成 upper_bound = 105.11 —— 一个"置信度 105 分"的区间。
        只钳点估计挡不住它: 加分会从区间里漏回来 (见 e2_report_gen.py:179)。
        """
        result = self._run(
            probabilities={"多元化业务拉动": 0.35, "补库存推升周转": 0.35,
                           "其他原因": 0.30},
            semantic_scores={"多元化业务拉动": 1, "补库存推升周转": 1,
                             "其他原因": 0},
        )
        detail = result.anomaly_details[0]

        # 手算: known = 0.35x(+1) + 0.35x(+1) = +0.70, m(Θ) = 0.30
        #   best_raw  = 0.70 + 0.30x(+2) = +1.30 -> 5.0 x 1.30 = +6.5  <- 未钳位
        #   worst_raw = 0.70 + 0.30x(-3) = -0.20 -> 5.0 x -0.20 = -1.0
        assert detail.score_pl == 0.0
        assert detail.score_bel == pytest.approx(-1.0)

        # 用户可见的不变式: 信任区间上界不得超过满分
        assert 100.0 + sum(d.score_pl for d in result.anomaly_details) <= 100.0


class TestUnattributedAnomaly:
    """「其他原因」= D2 未摊回的未知质量 Θ，不得当成中性归因洗掉扣分。

    回归: 曾经 D3 给「其他原因」语义分 0，E1 于是算出扣 0 分 ->
    检出 abnormal 异常却给满分 + 高置信度（fail-open）。
    """

    @staticmethod
    def _ctx(probabilities: dict, semantic_scores: dict) -> PipelineContext:
        ctx = PipelineContext()
        ctx.deviations = [
            DeviationAnomaly(
                indicator="毛利率", actual_value=0.0, benchmark_value=0.3,
                mad_multiple=6.0,  # W_phe = min(6/3, 5) = 2.0
                severity="abnormal",
            ),
        ]
        ctx.reasoning_results = [
            ProbabilityAssignment(
                anomaly_indicator="毛利率",
                anomaly_source="C",
                lookups=[], hypotheses=[],
                probabilities=probabilities,
                ds_metadata={"conflict_K": 0.0},  # delta = 1.0
                semantic_scores=semantic_scores,
                semantic_keywords={},
            ),
        ]
        return ctx

    def test_other_cause_is_uncertainty_not_a_cause(self):
        """苏美达形态: 100% 落「其他原因」-> 不扣分，但 m(Θ)=1，区间不得塌成一点

        区间是**单边**的：乐观端钳在 0。不确定性的表达靠悲观端向下展开 ——
        这也正是本类要守的东西（"未能归因"不得被洗成"没有问题"）。
        """
        ctx = self._ctx(
            probabilities={"其他原因": 1.0},
            semantic_scores={"其他原因": 0},
        )
        detail = run_scoring(ctx).anomaly_details[0]

        # 不计入归因明细（它不是一条原因）
        assert detail.causes == []
        # m(Θ) = 1.0: 完全未能归因
        assert detail.m_theta == 1.0
        # 点估计仍为 0（本次修复不改分数公式）
        assert detail.anomaly_score == 0.0
        # 但已知质量为 0 -> 区间按语义分全域 [-3,+2] **单向**展开：
        # 悲观端仍是全悲观 S_min=-3，乐观端被钳到 0（异常不能加分）
        assert detail.score_bel == pytest.approx(2.0 * -3.0)  # -6.0
        assert detail.score_pl == 0.0                        # 钳位，非 +4.0
        assert detail.score_pl != detail.score_bel           # 绝不塌成一点

    def test_other_cause_excluded_from_weighted_sum(self):
        """部分未知: 已知归因照常扣分，未知部分只进区间"""
        ctx = self._ctx(
            probabilities={"行业需求下降": 0.4, "其他原因": 0.6},
            semantic_scores={"行业需求下降": -2, "其他原因": 0},
        )
        detail = run_scoring(ctx).anomaly_details[0]

        assert [c.cause for c in detail.causes] == ["行业需求下降"]
        assert detail.m_theta == pytest.approx(0.6)
        # weighted_sum = 0.4 x (-2) = -0.8 -> 2.0 x -0.8 = -1.6
        assert detail.anomaly_score == pytest.approx(-1.6)
        # 区间 = 把 m(Θ) 整块改派到语义分全域，不得再除以 known:
        # best  = 0.4x(-2) + 0.6x(+2) = +0.4 -> 2.0 x +0.4 = +0.8 -> 钳到 0
        # worst = 0.4x(-2) + 0.6x(-3) = -2.6 -> 2.0 x -2.6 = -5.2
        # 钳位是**单向**的: 悲观端与钳位前逐字相同, 只有乐观端被封顶。
        # 于是旧的宽度等式 w_phe x (S_max - S_min) x m(Θ) = 6.0 不再成立 ——
        # 它度量的是跨零对称的散布, 而那多出来的 +0.8 正是"异常倒加分"。
        # 现在不确定性的下界仍是 0（最乐观 = 本条不扣分）。
        assert detail.score_bel == pytest.approx(-5.2)
        assert detail.score_pl == 0.0

    def test_allocation_is_not_double_counted(self):
        """「其他原因」的概率只进 m(Θ)，不得同时留在加权和里"""
        only_known = self._ctx(
            probabilities={"行业需求下降": 1.0},
            semantic_scores={"行业需求下降": -2},
        )
        with_unknown = self._ctx(
            probabilities={"行业需求下降": 0.4, "其他原因": 0.6},
            semantic_scores={"行业需求下降": -2, "其他原因": 0},
        )
        d1 = run_scoring(only_known).anomaly_details[0]
        d2 = run_scoring(with_unknown).anomaly_details[0]

        # 已知归因概率从 1.0 降到 0.4，扣分相应变小
        assert d1.anomaly_score == pytest.approx(-4.0)   # 2.0 x (1.0 x -2)
        assert d2.anomaly_score == pytest.approx(-1.6)
        assert d1.m_theta == 0.0 and d2.m_theta == pytest.approx(0.6)


class TestTrustIntervalStability:
    """信任区间不得发散。

    回归: 曾经 best/worst 被除以 known(=1-m(Θ))。但 mass_weighted_raw 是
    Σ P_i x S_i（分数加权和，非质量之和），再除一次 known 等于把区间放大
    1/known 倍；m(Θ) -> 1 时 known -> 0，区间发散。实测苏美达「净资产收益率」
    产出 [-3091.25, +2565.57] —— 一个 0-100 分制下不可能出现的数。
    """

    @staticmethod
    def _run(probabilities: dict, semantic_scores: dict, mass_final: dict | None = None,
             w_phe_mad: float = 6.0):
        """w_phe_mad=6.0 -> W_phe = min(6/3, 5) = 2.0, delta = 1.0 (K=0)"""
        ctx = PipelineContext()
        ctx.deviations = [
            DeviationAnomaly(
                indicator="净资产收益率", actual_value=0.0, benchmark_value=0.3,
                mad_multiple=w_phe_mad, severity="abnormal",
            ),
        ]
        ds = {"conflict_K": 0.0}
        if mass_final is not None:
            ds["mass_final"] = mass_final
        ctx.reasoning_results = [
            ProbabilityAssignment(
                anomaly_indicator="净资产收益率", anomaly_source="C",
                lookups=[], hypotheses=[],
                probabilities=probabilities,
                ds_metadata=ds,
                semantic_scores=semantic_scores,
                semantic_keywords={},
            ),
        ]
        return run_scoring(ctx).anomaly_details[0]

    def test_theta_is_not_double_counted(self):
        """m(Θ) 只能取一处: mass_final["Θ"] 与 probabilities["其他原因"] 是同一份质量

        实测苏美达「净资产收益率」: 两者同为 0.4978，相加得 0.9956（真实 0.4978）。
        """
        probs = {"原因A": 0.25, "原因B": 0.2522, "其他原因": 0.4978}
        detail = self._run(
            probs, {"原因A": -1, "原因B": -1, "其他原因": 0},
            mass_final={"原因A": 0.25, "原因B": 0.2522, "Θ": 0.4978},
        )

        assert detail.m_theta == pytest.approx(0.4978)   # 不是 0.9956
        # 自洽性: m(Θ) 必须等于已知概率之和的补
        known = sum(v for k, v in probs.items() if k != "其他原因")
        assert detail.m_theta == pytest.approx(1.0 - known)

    def test_interval_stays_within_semantic_range(self):
        """不变量: score_pl <= W_phe x S_max, score_bel >= W_phe x S_min

        推导: best = Σ P_i S_i + m(Θ) S_max <= S_max x (Σ P_i + m(Θ)) = S_max，
        因为 probabilities 归一化 (Σ=1)。多除一个 known 会破坏此式。
        """
        probs = {"原因A": 0.25, "原因B": 0.2522, "其他原因": 0.4978}
        detail = self._run(probs, {"原因A": -1, "原因B": -1, "其他原因": 0})

        w_phe = detail.w_phe
        assert detail.score_pl <= w_phe * 2.0 + 1e-9
        assert detail.score_bel >= w_phe * -3.0 - 1e-9

    def test_extreme_unknown_still_bounded(self):
        """m(Θ) -> 1 也不得发散（旧实现此处会除以 known≈0）"""
        detail = self._run(
            {"其他原因": 0.9999, "原因A": 0.0001},
            {"原因A": -3, "其他原因": 0},
        )
        w_phe = detail.w_phe
        assert detail.m_theta == pytest.approx(0.9999)
        assert detail.score_pl <= w_phe * 2.0 + 1e-9
        assert detail.score_bel >= w_phe * -3.0 - 1e-9

    def test_point_interval_needs_no_special_case(self):
        """known == 0 时无需特判: Σ P_i = 0，公式自然退化到语义分全域"""
        detail = self._run({"其他原因": 1.0}, {"其他原因": 0})
        w_phe = detail.w_phe
        # 悲观端退化为全悲观 S_min=-3；乐观端退化到 S_max=+2 后再被钳到 0
        assert detail.score_bel == pytest.approx(w_phe * -3.0)
        assert detail.score_pl == 0.0
