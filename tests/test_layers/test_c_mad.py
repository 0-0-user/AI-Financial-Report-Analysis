"""
==========================================================
 tests/test_layers/test_c_mad.py — MAD 算法测试
==========================================================

测试 C 层中位数绝对偏差（MAD）算法的正确性：
- 正常数据下 MAD 应对极端值稳健（不受单个异常值影响）
- 全相同值时 MAD 应为 0
- 大幅偏离中位数的值应被正确检测为异常
"""

import pytest
from layers.layer_c_deviation.mad_calculator import calc_mad, calc_deviation_multiple


class TestMADCalculator:
    """MAD 核心算法测试"""

    def test_mad_normal_case(self):
        """正常数据：MAD 应对极端值稳健"""
        values = [10, 12, 11, 13, 100]  # 100 是异常值
        mad = calc_mad(values)
        # MAD 应接近 1（忽略 100），而不是被拉高
        assert mad < 2.0

    def test_mad_all_same(self):
        """全相同值：MAD 应为 0"""
        values = [5, 5, 5, 5]
        mad = calc_mad(values)
        assert mad == 0.0

    def test_deviation_detection(self):
        """大幅偏离中位数的指标"""
        peer_values = [10, 12, 11, 13, 12]
        actual = 30  # 大幅偏离
        median, mad, multiple = calc_deviation_multiple(actual, peer_values)
        assert multiple > 5.0  # 应检测为极端异常
