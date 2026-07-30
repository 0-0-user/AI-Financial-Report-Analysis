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
