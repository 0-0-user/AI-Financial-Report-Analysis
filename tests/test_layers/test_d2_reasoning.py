"""
==========================================================
 tests/test_layers/test_d2_reasoning.py — D2 D-S 证据理论测试
==========================================================

测试 D2 层的 D-S 证据理论核心逻辑（纯数学，无 LLM）：
- _build_m1 / _build_m2 mass 构造
- _dempster_combine 合成
- _pignistic_transform 概率转换
- _calc_source_authority / _calc_source_count_bonus 辅助函数
- 完整 run_probability_allocation 集成
"""

import pytest

from layers.layer_d_reasoning.d2_probability import (
    _build_m1,
    _build_m2,
    _dempster_combine,
    _build_conflict_map,
    _pignistic_transform,
    _calc_source_authority,
    _calc_source_count_bonus,
    _calc_consensus_weight,
    _extract_indicator,
    run_probability_allocation,
)
from schemas.anomaly import LogicAnomaly, DeviationAnomaly
from schemas.reasoning import Explanation, Hypothesis


# ============================================================
# _build_m1 — 路1 mass 分配
# ============================================================

class TestBuildM1:
    def test_empty(self):
        """k=0 → m₁(Θ)=1.0"""
        m1 = _build_m1([], epsilon_1=0.35)
        assert m1 == {"Θ": 1.0}

    def test_single(self):
        """k=1 → m₁({E})=0.65, m₁(Θ)=0.35"""
        causes = [{"name": "行业周期下行", "path1_indices": [0], "path2_indices": [], "conflicts_with": []}]
        m1 = _build_m1(causes, epsilon_1=0.35)
        assert m1["行业周期下行"] == pytest.approx(0.65, rel=1e-4)
        assert m1["Θ"] == pytest.approx(0.35, rel=1e-4)
        assert abs(sum(m1.values()) - 1.0) < 1e-6

    def test_two(self):
        """k=2 → 73开: 0.455 + 0.195 + 0.35 = 1.0"""
        causes = [
            {"name": "主要归因", "path1_indices": [0], "path2_indices": [], "conflicts_with": []},
            {"name": "次要归因", "path1_indices": [1], "path2_indices": [], "conflicts_with": []},
        ]
        m1 = _build_m1(causes, epsilon_1=0.35)
        assert m1["主要归因"] == pytest.approx(0.455, rel=1e-4)
        assert m1["次要归因"] == pytest.approx(0.195, rel=1e-4)
        assert m1["Θ"] == pytest.approx(0.35, rel=1e-4)
        assert abs(sum(m1.values()) - 1.0) < 1e-6

    def test_three_exponential_decay(self):
        """k≥3 → 指数衰减: 1/2, 1/4, rest"""
        causes = [
            {"name": "A", "path1_indices": [0], "path2_indices": [], "conflicts_with": []},
            {"name": "B", "path1_indices": [1], "path2_indices": [], "conflicts_with": []},
            {"name": "C", "path1_indices": [2], "path2_indices": [], "conflicts_with": []},
        ]
        m1 = _build_m1(causes, epsilon_1=0.35)
        # pool = 0.65: A=0.325, B=0.1625, C=0.1625
        assert m1["A"] == pytest.approx(0.325, rel=1e-4)
        assert m1["B"] == pytest.approx(0.1625, rel=1e-4)
        assert m1["C"] == pytest.approx(0.1625, rel=1e-4)
        assert m1["Θ"] == pytest.approx(0.35, rel=1e-4)
        assert abs(sum(m1.values()) - 1.0) < 1e-6


# ============================================================
# _build_m2 — 路2 mass 分配
# ============================================================

class TestBuildM2:
    def test_empty(self):
        """k=0 → m₂(Θ)=1.0"""
        m2 = _build_m2([], [], epsilon_2=0.30)
        assert m2 == {"Θ": 1.0}

    def test_single(self):
        """单一路2归因 → 全部 pool 归它"""
        original_hypotheses = [
            Hypothesis(hypothesis="需求下降", reasoning="行业周期下行", source="券商深度报告", confidence_rank=1),
        ]
        causes = [
            {"name": "需求下降", "path1_indices": [], "path2_indices": [0], "conflicts_with": []},
        ]
        m2 = _build_m2(causes, original_hypotheses, epsilon_2=0.30)
        assert "需求下降" in m2
        assert m2["需求下降"] == pytest.approx(0.70, rel=1e-4)
        assert m2["Θ"] == pytest.approx(0.30, rel=1e-4)
        assert abs(sum(m2.values()) - 1.0) < 1e-6

    def test_multi_normalized_top5(self):
        """多个归因 → 按 consensus_weight 归一化后分配 0.70"""
        original_hypotheses = [
            Hypothesis(hypothesis="需求下降", reasoning="", source="券商深度报告", confidence_rank=1),
            Hypothesis(hypothesis="成本上升", reasoning="", source="行业新闻", confidence_rank=2),
            Hypothesis(hypothesis="政策影响", reasoning="", source="股吧", confidence_rank=3),
        ]
        causes = [
            {"name": "需求下降", "path1_indices": [], "path2_indices": [0], "conflicts_with": []},
            {"name": "成本上升", "path1_indices": [], "path2_indices": [1], "conflicts_with": []},
            {"name": "政策影响", "path1_indices": [], "path2_indices": [2], "conflicts_with": []},
        ]
        m2 = _build_m2(causes, original_hypotheses, epsilon_2=0.30)
        # 券商=1.0, 新闻=0.7, 股吧=0.3 → cw 归一化后 ×0.70
        assert "需求下降" in m2
        assert "成本上升" in m2
        assert "政策影响" in m2
        assert m2["Θ"] == pytest.approx(0.30, rel=1e-4)
        # 需求下降权重最高
        assert m2["需求下降"] > m2["成本上升"]
        assert m2["成本上升"] > m2["政策影响"]
        assert abs(sum(m2.values()) - 1.0) < 1e-6

    def test_top5_filter(self):
        """超过 5 条时只保留前 5 """
        original_hypotheses = [
            Hypothesis(hypothesis=f"H{i}", reasoning="", source=s, confidence_rank=i)
            for i, s in enumerate(["券商深度报告", "行业新闻", "股吧", "行业新闻", "行业常识", "自媒体", "A0宏观事实"])
        ]
        causes = [
            {"name": f"H{i}", "path1_indices": [], "path2_indices": [i], "conflicts_with": []}
            for i in range(7)
        ]
        m2 = _build_m2(causes, original_hypotheses, epsilon_2=0.30)
        # 最多 5 个归因名 + Θ
        # 券商(1.0)、A0宏观(0.6)、行业新闻(0.7×2)、行业常识(0.5) → 前5名
        cause_count = sum(1 for k in m2 if k != "Θ")
        assert cause_count <= 5, f"Expected at most 5 causes, got {cause_count}"


# ============================================================
# 辅助函数测试
# ============================================================

class TestSourceAuthority:
    def test_authority_values(self):
        assert _calc_source_authority("券商深度报告") == 1.0
        assert _calc_source_authority("官方公告") == 1.0
        assert _calc_source_authority("行业新闻") == 0.7
        assert _calc_source_authority("财经媒体") == 0.7
        assert _calc_source_authority("A0宏观事实") == 0.6
        assert _calc_source_authority("行业常识") == 0.5
        assert _calc_source_authority("雪球") == 0.3
        assert _calc_source_authority("股吧") == 0.3
        assert _calc_source_authority("自媒体") == 0.3
        assert _calc_source_authority("纯LLM猜测") == 0.1

    def test_combined_source(self):
        """复合来源取最高权威性"""
        assert _calc_source_authority("券商深度报告、行业新闻") == 1.0
        assert _calc_source_authority("行业新闻、股吧") == 0.7


class TestSourceCountBonus:
    def test_count_bonus_values(self):
        assert _calc_source_count_bonus(5) == 1.0
        assert _calc_source_count_bonus(3) == 1.0
        assert _calc_source_count_bonus(2) == 0.7
        assert _calc_source_count_bonus(1) == 0.4
        assert _calc_source_count_bonus(0) == 0.2


class TestCalcConsensusWeight:
    def test_single_source(self):
        """券商深度报告 × 1 来源 = 1.0 × 0.4 = 0.4"""
        cause = {"name": "T", "path1_indices": [], "path2_indices": [0], "conflicts_with": []}
        hypos = [Hypothesis(hypothesis="T", reasoning="", source="券商深度报告", confidence_rank=1)]
        assert _calc_consensus_weight(cause, hypos) == pytest.approx(0.4, rel=1e-4)

    def test_three_sources(self):
        """券商深度报告 + 行业新闻 + A0宏观事实: 权威性=1.0, count=3→×1.0 = 1.0"""
        cause = {"name": "T", "path1_indices": [], "path2_indices": [0, 1, 2], "conflicts_with": []}
        hypos = [
            Hypothesis(hypothesis="T", reasoning="", source="券商深度报告", confidence_rank=1),
            Hypothesis(hypothesis="T", reasoning="", source="行业新闻", confidence_rank=2),
            Hypothesis(hypothesis="T", reasoning="", source="A0宏观事实", confidence_rank=3),
        ]
        assert _calc_consensus_weight(cause, hypos) == pytest.approx(1.0, rel=1e-4)


# ============================================================
# _dempster_combine — Dempster 合成
# ============================================================

class TestDempsterCombine:
    def test_no_conflict(self):
        """双方部分重合，无冲突 → K=0"""
        m1 = {"原因A": 0.65, "Θ": 0.35}
        m2 = {"原因A": 0.35, "原因B": 0.21, "原因C": 0.14, "Θ": 0.30}
        cmap = {}
        mass_final, K = _dempster_combine(m1, m2, cmap)
        assert K == pytest.approx(0.0, rel=1e-4)
        assert "原因A" in mass_final
        assert "原因B" in mass_final
        assert "原因C" in mass_final
        assert abs(sum(mass_final.values()) - 1.0) < 1e-6
        # 双方都支持 A → A 最大
        assert mass_final["原因A"] > mass_final["原因B"]

    def test_with_conflict(self):
        """一方说 A，一方说 fraud，互斥 → K > 0"""
        m1 = {"正常经营波动": 0.65, "Θ": 0.35}
        m2 = {"财务造假": 0.49, "其他因素": 0.21, "Θ": 0.30}
        cmap = {"正常经营波动": {"财务造假"}, "财务造假": {"正常经营波动"}}
        mass_final, K = _dempster_combine(m1, m2, cmap)
        assert K > 0
        assert abs(sum(mass_final.values()) - 1.0) < 1e-6

    def test_full_agreement(self):
        """双方都指向同一归因 → mass 增强"""
        m1 = {"原因A": 0.65, "Θ": 0.35}
        m2 = {"原因A": 0.70, "Θ": 0.30}
        cmap = {}
        mass_final, K = _dempster_combine(m1, m2, cmap)
        assert K == pytest.approx(0.0, rel=1e-4)
        # m(A) = (0.65×0.70 + 0.65×0.30 + 0.35×0.70) / 1.0 = 0.455+0.195+0.245 = 0.895
        assert mass_final["原因A"] == pytest.approx(0.895, rel=1e-4)
        assert mass_final["Θ"] == pytest.approx(0.105, rel=1e-4)

    def test_high_conflict_detected(self):
        """高冲突：K > 0.6"""
        m1 = {"正常经营": 0.80, "Θ": 0.20}
        m2 = {"财务造假": 0.85, "Θ": 0.15}
        cmap = {"正常经营": {"财务造假"}, "财务造假": {"正常经营"}}
        mass_final, K = _dempster_combine(m1, m2, cmap)
        assert K > 0.6
        # 仍然合成，K 不阻断
        assert abs(sum(mass_final.values()) - 1.0) < 1e-6

    def test_both_empty(self):
        """双方都只有 Θ → m_final(Θ)=1.0, K=0"""
        m1 = {"Θ": 1.0}
        m2 = {"Θ": 1.0}
        cmap = {}
        mass_final, K = _dempster_combine(m1, m2, cmap)
        assert K == pytest.approx(0.0, rel=1e-4)
        assert mass_final.get("Θ", 0) == pytest.approx(1.0, rel=1e-4)

    def test_one_side_empty(self):
        """单方有结果 → 另一方 Θ 相当于 identity"""
        m1 = {"原因A": 0.65, "Θ": 0.35}
        m2 = {"Θ": 1.0}
        cmap = {}
        mass_final, K = _dempster_combine(m1, m2, cmap)
        assert K == pytest.approx(0.0, rel=1e-4)
        assert mass_final["原因A"] == pytest.approx(0.65, rel=1e-4)
        assert mass_final["Θ"] == pytest.approx(0.35, rel=1e-4)

    def test_complete_conflict(self):
        """完全冲突：m₁(A)=1.0, m₂(B)=1.0, A↔B → K=1.0"""
        m1 = {"A": 1.0}
        m2 = {"B": 1.0}
        cmap = {"A": {"B"}, "B": {"A"}}
        mass_final, K = _dempster_combine(m1, m2, cmap)
        assert K == pytest.approx(1.0, rel=1e-4)
        assert mass_final.get("Θ", 0) == pytest.approx(1.0, rel=1e-4)


# ============================================================
# _pignistic_transform
# ============================================================

class TestPignisticTransform:
    def test_basic(self):
        """m(Θ) 按比例分摊到各归因"""
        mass = {"原因A": 0.60, "原因B": 0.15, "Θ": 0.25}
        probs = _pignistic_transform(mass)
        # prob(A) = 0.60/(1-0.25) = 0.80, prob(B) = 0.15/0.75 = 0.20
        assert probs["原因A"] == pytest.approx(0.80, rel=1e-4)
        assert probs["原因B"] == pytest.approx(0.20, rel=1e-4)
        assert abs(sum(probs.values()) - 1.0) < 1e-6
        assert "Θ" not in probs

    def test_all_uncertainty(self):
        """全 Θ → 返回 '其他原因'"""
        probs = _pignistic_transform({"Θ": 1.0})
        assert probs == {"其他原因": 1.0}

    def test_sum_to_one(self):
        """随机 mass 验证概率和为 1"""
        mass = {"A": 0.35, "B": 0.25, "C": 0.10, "Θ": 0.30}
        probs = _pignistic_transform(mass)
        assert abs(sum(probs.values()) - 1.0) < 1e-6


# ============================================================
# _build_conflict_map
# ============================================================

class TestBuildConflictMap:
    def test_bidirectional(self):
        """conflicts_with 自动双向映射"""
        causes = [
            {"name": "A", "path1_indices": [0], "path2_indices": [], "conflicts_with": ["B"]},
            {"name": "B", "path1_indices": [], "path2_indices": [0], "conflicts_with": []},
        ]
        cmap = _build_conflict_map(causes)
        assert "B" in cmap["A"]
        assert "A" in cmap["B"]

    def test_no_conflicts(self):
        """没有冲突时映射为空"""
        causes = [
            {"name": "A", "path1_indices": [0], "path2_indices": [], "conflicts_with": []},
            {"name": "B", "path1_indices": [], "path2_indices": [0], "conflicts_with": []},
        ]
        cmap = _build_conflict_map(causes)
        assert cmap == {"A": set(), "B": set()}


# ============================================================
# _extract_indicator
# ============================================================

class TestExtractIndicator:
    def test_logic_anomaly(self):
        anomaly = LogicAnomaly(check_name="net_profit_cash_ratio", value=0.3, threshold=0.6, severity=2.0, summary="测试")
        assert _extract_indicator(anomaly) == "net_profit_cash_ratio"

    def test_deviation_anomaly(self):
        anomaly = DeviationAnomaly(indicator="存货周转率", actual_value=0.3, benchmark_value=0.8, mad_multiple=3.5, severity="extreme")
        assert _extract_indicator(anomaly) == "存货周转率"

    def test_unknown(self):
        class Fake:
            pass
        assert _extract_indicator(Fake()) == "未知指标"


# ============================================================
# 集成测试：run_probability_allocation
# ============================================================

class TestRunProbabilityAllocation:
    def test_empty_reasoning_results(self):
        """空输入不崩溃"""
        result = run_probability_allocation([])
        assert result == []

    def test_single_anomaly_with_mocked_merge(self, monkeypatch):
        """单个异常，mock LLM 合并 → 验证输出结构完整"""
        def mock_merge(indicator, lookups, hypotheses):
            return [{"name": "存货增加", "path1_indices": [0], "path2_indices": [0], "conflicts_with": []}]
        monkeypatch.setattr(
            "layers.layer_d_reasoning.d2_probability._llm_merge_conflict",
            mock_merge,
        )

        lookups = [
            Explanation(summary="存货增加", source_text="主要是加大备货力度所致", page_number=15),
        ]
        hypotheses = [
            Hypothesis(hypothesis="战略备货", reasoning="销售增长带动库存提升", source="券商深度报告", confidence_rank=1),
        ]
        results = [{
            "source": "C",
            "anomaly": DeviationAnomaly(
                indicator="存货周转率",
                actual_value=0.3,
                benchmark_value=0.8,
                mad_multiple=3.5,
                severity="extreme",
            ),
            "lookup": lookups,
            "hypotheses": hypotheses,
        }]
        assignments = run_probability_allocation(results)
        assert len(assignments) == 1
        pa = assignments[0]
        assert pa.anomaly_indicator == "存货周转率"
        assert pa.anomaly_source == "C"
        assert abs(sum(pa.probabilities.values()) - 1.0) < 1e-6
        assert pa.ds_metadata is not None
        assert "m1" in pa.ds_metadata
        assert "m2" in pa.ds_metadata
        assert "conflict_K" in pa.ds_metadata

    def test_ds_metadata_contains_k(self, monkeypatch):
        """验证 ds_metadata 包含冲突系数"""
        def mock_merge(indicator, lookups, hypotheses):
            return [{"name": "存货增加", "path1_indices": [0], "path2_indices": [0], "conflicts_with": []}]
        monkeypatch.setattr(
            "layers.layer_d_reasoning.d2_probability._llm_merge_conflict",
            mock_merge,
        )

        lookups = [
            Explanation(summary="存货增加", source_text="加大备货", page_number=15),
        ]
        hypotheses = [
            Hypothesis(hypothesis="积压滞销", reasoning="销售不畅", source="行业新闻", confidence_rank=1),
        ]
        results = [{
            "source": "C",
            "anomaly": DeviationAnomaly(
                indicator="存货周转率", actual_value=0.3, benchmark_value=0.8,
                mad_multiple=3.5, severity="extreme",
            ),
            "lookup": lookups,
            "hypotheses": hypotheses,
        }]
        assignments = run_probability_allocation(results)
        pa = assignments[0]
        meta = pa.ds_metadata
        assert isinstance(meta.get("conflict_K"), (int, float))
        assert isinstance(meta.get("severe_conflict"), bool)
