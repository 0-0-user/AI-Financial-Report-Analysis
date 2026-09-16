"""B+ 自己的权重表 (config/bplus_weights.yaml) 与 calc_severity 的接线

接缝:
  load_bplus_weights()                       -> {检查名: {category, base_weight, direction, amplification_cap}}
  calc_severity(check_name, value, ...)      -> 曲线 raw x base_weight, 钳到 [0, cap]
  calc_internal_control_severity(missing)    -> 内控体系那一档 (不走 calc_severity 的曲线)

期望值一律是**手算的曲线值 + yaml 里写死的数字**, 不在测试里重算被测公式
(重算公式 = tautological, 永远绿)。
"""

import logging
from pathlib import Path

import pytest
import yaml

from layers.layer_bplus_internal.logic_checks import (
    calc_internal_control_severity,
    calc_severity,
    load_bplus_weights,
)

_ROOT = Path(__file__).resolve().parent.parent.parent
_WEIGHTS_YAML = _ROOT / "config" / "bplus_weights.yaml"

# 规格里给定的权重表 —— 独立于实现写在测试里, 不从被测模块读
_SPEC = {
    "利润含金量异常": ("现金流质量", 2.0, "negative", 5.0),
    "资金结构异常": ("资金管控", 1.5, "negative", 5.0),
    "资金管控异常": ("资金管控", 1.5, "negative", 5.0),
    "过度扩张嫌疑": ("成长能力", 1.0, "negative", 4.0),
    "过度投资风险": ("成长能力", 1.0, "negative", 4.0),
    "民企扩张风险": ("成长能力", 1.0, "negative", 4.0),
    "销售现金背离": ("现金流质量", 1.5, "negative", 4.0),
    "内控体系": ("内控", 1.0, "negative", 3.0),
}

# 与旧版 weights.yaml 不同: 那张表的 28 个 key 与 B+ 检查名零交集, 且全仓 .py 零引用。
_LEGACY_WEIGHTS_YAML = _ROOT / "config" / "weights.yaml"


def _as_tuples(table: dict) -> dict:
    return {
        name: (
            cfg["category"],
            cfg["base_weight"],
            cfg["direction"],
            cfg["amplification_cap"],
        )
        for name, cfg in table.items()
    }


class TestWeightsTableIsDeclared:
    def test_yaml_file_declares_exactly_the_specified_table(self):
        doc = yaml.safe_load(_WEIGHTS_YAML.read_text(encoding="utf-8"))
        assert _as_tuples(doc["checks"]) == _SPEC

    def test_loader_exposes_the_same_table(self):
        assert _as_tuples(load_bplus_weights()) == _SPEC

    def test_table_is_not_the_legacy_weights_file(self):
        """老表的 key 全是财务比率名, 与 B+ 检查名零交集 —— 不能拿它顶包"""
        legacy = yaml.safe_load(_LEGACY_WEIGHTS_YAML.read_text(encoding="utf-8"))
        legacy_keys = set(legacy.get("weights", {}))
        assert not (legacy_keys & set(_SPEC)), (
            "前提变了: 老 weights.yaml 现在与 B+ 检查名有交集, 需要重新讨论是否合并"
        )


class TestGrowthFamilyUsesItsOwnCap:
    """过度扩张嫌疑 / 过度投资风险 / 民企扩张风险: base_weight=1.0, cap=4.0

    曲线 raw = min(<cap>, max(1.5, |value| * 8))。旧代码把上限写死成 5.0。
    """

    def test_raw_above_the_old_cap_is_now_capped_at_four(self):
        # value=-0.70 -> |value|*8 = 5.6 -> min(4.0, 5.6) = 4.0 -> x1.0 -> clamp[0,4] = 4.0
        # 旧行为: min(5.0, 5.6) = 5.0
        assert calc_severity("过度扩张嫌疑", -0.70) == pytest.approx(4.0)

    def test_curve_floor_is_untouched(self):
        # value=-0.05 -> |value|*8 = 0.40 -> max(1.5, 0.40) = 1.5 -> min(4.0, 1.5) = 1.5
        # 下限 1.5 是曲线形状的一部分, 本轮不动它
        assert calc_severity("过度扩张嫌疑", -0.05) == pytest.approx(1.5)


class TestProfitQualityIsWeighted:
    """利润含金量异常: base_weight=2.0, cap=5.0

    曲线 (industry=None -> nc_p25 取默认 0.25):
      raw = min(cap, (0.25 - value) / max(0.25*0.3, 0.05) * 3)
    """

    def test_weight_doubles_the_curve_output(self):
        # value=0.20 -> (0.25-0.20)/0.075 = 0.6667 -> x3 = 2.0 -> min(5.0, 2.0) = 2.0
        # x2.0 = 4.0 -> clamp[0,5] = 4.0   (旧行为: 2.0)
        assert calc_severity("利润含金量异常", 0.20) == pytest.approx(4.0)

    def test_weighted_result_is_clamped_to_the_cap(self):
        # value=-0.10 -> 曲线走 value<0 分支, raw = cap = 5.0
        # x2.0 = 10.0 -> clamp[0,5] = 5.0
        assert calc_severity("利润含金量异常", -0.10) == pytest.approx(5.0)

    def test_a_zero_raw_stays_zero_after_weighting(self):
        # value=0.30 >= nc_p25(0.25) -> 曲线判无异常, raw = 0.0
        # 0.0 x 2.0 = 0.0 —— 加权不能把「无异常」变成扣分
        assert calc_severity("利润含金量异常", 0.30) == pytest.approx(0.0)


class TestDualHighIsWeighted:
    """资金结构异常: base_weight=1.5, cap=5.0

    曲线 (value = 货币资金/总资产 x 有息负债/总资产):
      0.10 < p <= 0.30 -> 2.0 + (p - 0.10)/0.20 * 3.0
      p <= 0.10        -> max(0.5, p/0.10 * 2.0)
    """

    def test_weight_scales_the_middle_segment(self):
        # p=0.15 -> 2.0 + 0.05/0.20*3.0 = 2.75 -> x1.5 = 4.125 -> clamp[0,5] = 4.125
        # 旧行为: 2.75
        assert calc_severity("资金结构异常", 0.15) == pytest.approx(4.125)

    def test_weight_scales_the_low_segment(self):
        # p=0.05 -> max(0.5, 0.05/0.10*2.0 = 1.0) = 1.0 -> x1.5 = 1.5
        # 旧行为: 1.0
        assert calc_severity("资金结构异常", 0.05) == pytest.approx(1.5)

    def test_the_curve_is_continuous_at_its_own_breakpoint(self):
        # p=0.30 -> 2.0 + 0.20/0.20*3.0 = 5.0 = cap -> x1.5 = 7.5 -> clamp 5.0
        assert calc_severity("资金结构异常", 0.30) == pytest.approx(5.0)


class TestFundControlIsWeighted:
    """资金管控异常: base_weight=1.5, cap=5.0

    曲线 (value = 母子资金分离度):
      value > 0.30 -> cap
      value > 0.15 -> 3.0
      else         -> max(0.0, value/0.15 * 1.5)
    """

    def test_weight_scales_the_middle_plateau(self):
        # value=0.20 -> 3.0 -> x1.5 = 4.5 -> clamp[0,5] = 4.5   (旧行为: 3.0)
        assert calc_severity("资金管控异常", 0.20) == pytest.approx(4.5)

    def test_weight_scales_the_ramp(self):
        # value=0.05 -> 0.05/0.15*1.5 = 0.5 -> x1.5 = 0.75   (旧行为: 0.5)
        assert calc_severity("资金管控异常", 0.05) == pytest.approx(0.75)

    def test_the_top_plateau_saturates_at_the_cap(self):
        # value=0.40 -> 曲线顶格 raw = cap = 5.0 -> x1.5 = 7.5 -> clamp[0,5] = 5.0
        assert calc_severity("资金管控异常", 0.40) == pytest.approx(5.0)


class TestEveryGrowthNameReachesTheTable:
    """同一族曲线的另外三个名字必须各自拿到自己的那一行配置。

    只测 过度扩张嫌疑 是不够的 —— 名字写错/漏配时, calc_severity 会静默退回
    旧行为 (raw 不加权), 而不是报错。
    """

    def test_overinvestment_uses_weight_1_and_cap_4(self):
        # value=-0.70 -> min(4.0, 5.6) = 4.0 -> x1.0 -> 4.0   (旧行为: min(5.0, 5.6) = 5.0)
        assert calc_severity("过度投资风险", -0.70) == pytest.approx(4.0)

    def test_private_enterprise_expansion_uses_weight_1_and_cap_4(self):
        # value=-0.70 -> 4.0 -> x1.0 -> 4.0   (旧行为: 5.0)
        assert calc_severity("民企扩张风险", -0.70) == pytest.approx(4.0)

    def test_sales_cash_divergence_uses_weight_1_5_and_cap_4(self):
        # value=-0.70 -> raw 4.0 -> x1.5 = 6.0 -> clamp[0,4] = 4.0   (旧行为: 5.0)
        assert calc_severity("销售现金背离", -0.70) == pytest.approx(4.0)

    def test_sales_cash_divergence_weight_shows_below_the_cap(self):
        # value=-0.20 -> |value|*8 = 1.6 -> max(1.5, 1.6) = 1.6 -> min(4.0, 1.6) = 1.6
        # x1.5 = 2.4 -> clamp[0,4] = 2.4   (旧行为: 1.6)
        assert calc_severity("销售现金背离", -0.20) == pytest.approx(2.4)


class TestUnconfiguredNamesKeepOldBehaviour:
    """权重表里没有的名字 —— **保持旧行为并记 warning**。

    「没配置」不等于「不扣分」: 静默返回 0 会让一条真实异常凭空消失。
    calc_severity 的曲线按**别名组**写 (`净现比`/`利润含金量异常` 共用一条),
    但配置只给了其中一个名字 —— 别名正是最容易踩到这个坑的地方。
    """

    def test_alias_keeps_the_unweighted_curve_output_and_warns(self, caplog):
        with caplog.at_level(logging.WARNING):
            got = calc_severity("净现比", 0.20)
        # 曲线 raw = 2.0; 配置里没有 `净现比` -> 不加权, 不能变成 `利润含金量异常` 的 4.0
        assert got == pytest.approx(2.0)
        assert "净现比" in caplog.text

    def test_dual_high_alias_keeps_the_unweighted_curve_output(self):
        # `存贷双高` 共用 `资金结构异常` 的曲线; raw = 2.75, 不加权 -> 2.75 (不是 4.125)
        assert calc_severity("存贷双高", 0.15) == pytest.approx(2.75)

    def test_fund_separation_alias_keeps_the_unweighted_curve_output(self):
        # `母子资金分离度` 共用 `资金管控异常` 的曲线; raw = 3.0 -> 3.0 (不是 4.5)
        assert calc_severity("母子资金分离度", 0.20) == pytest.approx(3.0)

    def test_a_name_with_no_curve_at_all_still_returns_zero(self):
        """完全没听说的名字: 旧行为就是 0.0 —— 本轮不改变它, 但要留痕"""
        assert calc_severity("完全不存在的检查", 1.0) == pytest.approx(0.0)

    def test_every_legacy_curve_name_is_accounted_for(self):
        """把 calc_severity 支持的 7 个名字钉死 —— 以后加别名时这条会提醒你补配置"""
        names = (
            "净现比", "利润含金量异常",
            "存贷双高", "资金结构异常",
            "母子资金分离度", "资金管控异常",
            "过度扩张嫌疑", "过度投资风险", "民企扩张风险", "销售现金背离",
        )
        configured = {n for n in names if n in load_bplus_weights()}
        assert configured == {
            "利润含金量异常", "资金结构异常", "资金管控异常",
            "过度扩张嫌疑", "过度投资风险", "民企扩张风险", "销售现金背离",
        }


class TestInternalControlUsesItsOwnRow:
    """内控体系: base_weight=1.0, cap=3.0

    它**不走 calc_severity 的曲线** —— run_internal_control_check 用「缺失项计数」
    (0-4) 分级, 计数本身就是它的 raw。配置里为它留位, 同样的 weight/cap 作用在
    这个计数上。
    """

    def test_missing_count_is_the_raw_severity(self):
        # 1.0 x 1.0 = 1.0, 1.0 x 2.0 = 2.0 —— 都在 cap=3.0 之内
        assert calc_internal_control_severity(0) == pytest.approx(0.0)
        assert calc_internal_control_severity(1) == pytest.approx(1.0)
        assert calc_internal_control_severity(2) == pytest.approx(2.0)

    def test_cap_three_bites_on_the_worst_case(self):
        # 4 项全缺 (run_internal_control_check 的最大值) -> 4.0 x 1.0 = 4.0 -> clamp[0,3] = 3.0
        assert calc_internal_control_severity(4) == pytest.approx(3.0)

    def test_internal_control_does_not_go_through_calc_severity(self):
        """calc_severity 没有 `内控体系` 的曲线 —— 走它只会拿到 0.0"""
        assert calc_severity("内控体系", 4.0) == pytest.approx(0.0)

    def test_the_row_is_present_even_though_calc_severity_ignores_it(self):
        cfg = load_bplus_weights()["内控体系"]
        assert (cfg["base_weight"], cfg["amplification_cap"]) == (1.0, 3.0)
