"""C层稳健偏差算法测试 (v2: Sn + MAD + Pettitt + Bootstrap) """

import pytest
from layers.layer_c_deviation.mad_calculator import (
    calc_mad, calc_sn, calc_robust_scale, calc_deviation_multiple,
    pettitt_test, bootstrap_scale_ci,
)


class TestMADCalculator:
    """MAD 核心算法"""

    def test_mad_normal_case(self):
        values = [10, 12, 11, 13, 100]
        mad = calc_mad(values)
        assert mad < 3.0  # 对极端值稳健 (放宽到 3.0 因为 Sn 在 5 样本时偏大) 

    def test_mad_all_same(self):
        values = [5, 5, 5, 5]
        mad = calc_mad(values)
        assert mad == 0.0

    def test_deviation_detection(self):
        peer_values = [10, 12, 11, 13, 12]
        actual = 30
        median, scale, multiple, method = calc_deviation_multiple(actual, peer_values)
        assert multiple > 3.0  # Sn 在 5 个小样本中可能给出不同尺度，但远偏离应 > 3


class TestSnEstimator:
    """Sn 估计量 (Rousseeuw & Croux, 1993)"""

    def test_sn_normal(self):
        values = [10, 12, 11, 13, 12, 11, 10, 13, 12, 11]
        sn = calc_sn(values)
        assert 0.5 < sn < 3.0  # 正常数据 Sn 应接近 1

    def test_sn_outlier_robust(self):
        values = [10, 12, 11, 13, 12, 11, 10, 500, 12, 11]
        sn = calc_sn(values)
        # Sn 应不被 500 拉偏
        assert sn < 5.0

    def test_robust_scale_auto_select(self):
        values = list(range(10))
        scale, method = calc_robust_scale(values)
        assert method == "Sn"
        assert scale > 0


class TestPettittTest:
    """Pettitt 时序变点检验"""

    def test_no_change(self):
        # 完全随机，无趋势无突变
        values = [10, 11, 10, 15, 12, 9, 13, 11]
        p, idx = pettitt_test(values)
        assert p > 0.05  # 无突变

    def test_clear_change(self):
        values = [10, 11, 10, 12, 50, 52, 51, 53]
        p, idx = pettitt_test(values)
        assert p < 0.05  # 有明显突变


class TestBootstrap:
    """Bootstrap 置信区间"""

    def test_bootstrap_ci(self):
        values = [10, 12, 11, 13, 12, 11, 10, 13]
        lo, hi = bootstrap_scale_ci(values, n_bootstrap=500)
        assert lo > 0
        assert hi > lo
        assert hi < 10.0


# ============================================================
# 样本退化不得伪装成"极端异常"
# ============================================================

from schemas.benchmark import (
    Benchmark, IndustryProfile, PeerCompany, MIN_PEER_SAMPLE,
)
from schemas.financial import (
    FinancialStatement, FinancialField, ValidationResult, ValidationCheck,
)
from layers.layer_c_deviation.mad_calculator import run_deviation_analysis


def _fs() -> FinancialStatement:
    """营收 100 / 成本 80 -> 销售毛利率 XSMLL = 20%"""
    def f(name, value):
        return FinancialField(
            standard_name=name, raw_name=name, value=value,
            original_unit="元", report_type="合并报表",
        )
    return FinancialStatement(
        company_name="测试公司", stock_code="600710", year=2024,
        report_type="合并报表",
        balance_sheet={},
        income_statement={"Revenue_Total": f("Revenue_Total", 100.0),
                          "Cost_Revenue": f("Cost_Revenue", 80.0)},
        cashflow={},
        validation=ValidationResult(is_valid=True, checks=[
            ValidationCheck(check_name="t", passed=True, detail=""),
        ]),
    )


def _benchmark(peer_xsmll: list[float], median: float = 25.0) -> Benchmark:
    return Benchmark(
        industry=IndustryProfile(industry_name="贸易Ⅱ"),
        peer_median={"XSMLL": median},
        historical_mean={},
        peer_companies=[
            PeerCompany(name=f"同行{i}", stock_code=f"60000{i}",
                        similarity_score=0.9, financials={"XSMLL": v})
            for i, v in enumerate(peer_xsmll)
        ],
    )


class TestDegenerateScaleIsNotInfinity:
    """回归: robust scale 退化为 0 时旧实现返回 inf。

    下游 min(inf/3, 5.0) 直接顶格 -> W_phe=5.0 满格 -> 判 extreme。
    于是"同行只有自己一家"或"同行值全同"这种【测不出来】的状态，
    被渲染成最严重的异常，并推高扣分。
    """

    def test_all_peers_identical_and_actual_differs_is_unjudgeable(self):
        median, scale, multiple, method = calc_deviation_multiple(
            actual_value=20.0, peer_values=[25.0] * 5,
        )
        assert multiple is None          # 无法判定，而不是 inf
        assert scale == 0.0

    def test_all_peers_identical_and_actual_equals_median_is_zero(self):
        """实际值就是中位数时偏离确实是 0 —— 这是能判的，不该说测不出来"""
        median, scale, multiple, method = calc_deviation_multiple(
            actual_value=25.0, peer_values=[25.0] * 5,
        )
        assert multiple == 0.0

    def test_no_peers_is_unjudgeable(self):
        *_, multiple, _method = calc_deviation_multiple(20.0, [])
        assert multiple is None

    def test_self_comparison_is_zero_not_infinity(self):
        """池子里只剩目标公司自己: 中位数 = 自己 -> 偏离确实是 0，不是 inf。

        这是同类 bug 最纯粹的形态: 拿自己当自己的基准，差值恒等于 0，
        旧实现却因为 MAD=0 把它算成 inf -> 判 extreme -> W_phe 顶格。
        """
        _, scale, multiple, _ = calc_deviation_multiple(20.0, [20.0])
        assert scale == 0.0
        assert multiple == 0.0


class TestMinimumPeerSampleGuard:
    """样本不足时不做偏离判定，而不是给一个看起来很确定的结论"""

    def test_too_few_peers_yields_no_anomaly(self):
        bm = _benchmark([25.0, 26.0, 24.0])       # 3 家 < MIN_PEER_SAMPLE
        assert len(bm.peer_companies) < MIN_PEER_SAMPLE
        assert run_deviation_analysis(_fs(), bm) == []

    def test_identical_peers_yield_no_anomaly(self):
        """5 家同行值全同 -> scale=0 -> 不判定，而不是 inf 顶格"""
        bm = _benchmark([25.0] * MIN_PEER_SAMPLE)
        assert run_deviation_analysis(_fs(), bm) == []

    def test_enough_dispersed_peers_still_flags_extreme(self):
        """修复不得误伤正常路径: 样本充足且真偏离时仍要报 extreme"""
        bm = _benchmark([50.0, 52.0, 48.0, 51.0, 49.0, 50.5])   # 中位数 ~50.25
        anomalies = run_deviation_analysis(_fs(), bm)             # 实际 20%
        assert len(anomalies) == 1
        assert anomalies[0].severity == "extreme"
        assert anomalies[0].mad_multiple != float("inf")
        assert anomalies[0].mad_multiple == anomalies[0].mad_multiple  # 不是 NaN

    def test_no_infinite_multiple_ever_escapes(self):
        """扫一遍退化组合，确保 inf 不再从 C 层漏出去"""
        cases = [
            _benchmark([]),
            _benchmark([25.0]),
            _benchmark([25.0] * MIN_PEER_SAMPLE),
            _benchmark([25.0, 26.0, 24.0]),
        ]
        for bm in cases:
            for a in run_deviation_analysis(_fs(), bm):
                assert a.mad_multiple != float("inf")
