"""B+ 层顶层 fallback 阈值表测试

接缝:
  _load_industry_thresholds(industry) -> 该行业生效的阈值字典
  _get_ind_threshold(industry, key, default, financials) -> 单个阈值的最终取值

历史缺陷: `config/industry_thresholds.yaml` 的顶层有 `industries`(34 个行业)
与 `fallback`(14 个市场基准) 两个键, 但加载函数只缓存了 `industries` 子字典
(`yaml.safe_load(f).get("industries", {})`)。于是:
  - 行业不在表里 / 表里有键但整块没填值时, `_load_industry_thresholds`
    返回 `{}` 而不是顶层 fallback;
  - 那 14 个 fallback 阈值从来没被读过。

本文件钉住修复后的契约。期望值一律从 yaml 现读或按曲线手算, 不在测试里
重算被测公式。
"""

from pathlib import Path

import pytest
import yaml

from layers.layer_bplus_internal.logic_checks import (
    _get_ind_threshold,
    _load_industry_thresholds,
)

_ROOT = Path(__file__).resolve().parent.parent.parent
_THRESHOLDS_YAML = _ROOT / "config" / "industry_thresholds.yaml"

# 表里必然不存在的行业名 —— 用来代表"行业没被阈值表覆盖"的公司
_NO_SUCH_INDUSTRY = "__no_such_industry__"

# logic_checks 真正会去问的 7 个键 (调用点见 _check_cash_quality /
# _check_dual_high / _check_fund_separation / calc_severity)
_REQUESTED_KEYS = [
    "net_profit_cash_ratio_p75",
    "net_profit_cash_ratio_p25",
    "sales_cash_ratio_p75",
    "cash_to_assets_p75",
    "debt_to_assets_p75",
    "fund_separ_mean",
    "fund_separ_std",
]


def _doc() -> dict:
    """直读配置文件 —— 不借道被测模块的私有加载函数"""
    return yaml.safe_load(_THRESHOLDS_YAML.read_text(encoding="utf-8"))


def _fallback() -> dict:
    return _doc().get("fallback", {})


class TestFallbackIsReachable:
    def test_unknown_industry_returns_top_level_fallback(self):
        assert _load_industry_thresholds(_NO_SUCH_INDUSTRY) == _fallback()

    def test_non_live_industry_block_returns_top_level_fallback(self):
        """表里有这个键但整块是空的 (如被重复键覆盖掉的 `房地产开发`) —— 视同不存在"""
        cfg = _doc()["industries"].get("房地产开发")
        assert cfg == {}, "前提变了: 房地产开发 不再是空块, 换一个空块来测"
        assert _load_industry_thresholds("房地产开发") == _fallback()

    def test_live_industry_still_returns_its_own_block(self):
        """修复不能把行业专属阈值冲掉"""
        expected = _doc()["industries"]["白酒"]
        assert expected, "前提变了: 白酒 不再是有效行业块"
        assert _load_industry_thresholds("白酒") == expected

    def test_fallback_is_not_empty(self):
        """空 fallback 会让上面几条测试变成'两个空字典相等'的假绿"""
        assert len(_fallback()) == 14, sorted(_fallback())


class TestGetIndThresholdUsesFallback:
    def test_fallback_value_wins_over_the_passed_default(self):
        """传进去的 default 只该在连 fallback 都没有这个键时才生效"""
        fb = _fallback()
        assert _get_ind_threshold(
            _NO_SUCH_INDUSTRY, "net_profit_cash_ratio_p25", 99.0,
        ) == pytest.approx(fb["net_profit_cash_ratio_p25"])

    def test_a_key_the_code_never_asks_about_also_resolves(self):
        """p70 这类键代码当前不请求, 但 fallback 生效后一样取得到"""
        fb = _fallback()
        assert _get_ind_threshold(
            _NO_SUCH_INDUSTRY, "cash_to_assets_p70", 99.0,
        ) == pytest.approx(fb["cash_to_assets_p70"])

    def test_missing_key_still_falls_back_to_the_passed_default(self):
        """fallback 里没有的键 -> 才轮到 default"""
        assert _get_ind_threshold(
            _NO_SUCH_INDUSTRY, "no_such_key_at_all", 0.77,
        ) == pytest.approx(0.77)

    def test_every_key_the_code_requests_exists_in_the_fallback(self):
        """fallback 必须能独立支撑代码的全部请求 ——
        否则"回退到 fallback"只是把缺失从一层挪到另一层"""
        fb = _fallback()
        missing = [k for k in _REQUESTED_KEYS if k not in fb]
        assert not missing, f"这些代码在用的键没有 fallback: {missing}"


class TestP75IsDerivedFromP70:
    """表里只给了 p70 的行业, p75 由 p70 x 1.2 推算 —— 这条链在 fallback 生效后
    仍要走得通 (fallback 自己的键都齐, 所以这条链靠行业块来覆盖)。

    啤酒: cash_to_assets_p70 = 0.20, 表里没有 cash_to_assets_p75。
    """

    def test_derived_p75_beats_the_passed_default(self):
        p70 = _doc()["industries"]["啤酒"]["cash_to_assets_p70"]
        assert "cash_to_assets_p75" not in _doc()["industries"]["啤酒"], (
            "前提变了: 啤酒 现在直接给了 p75, 换一个只有 p70 的行业来测"
        )
        assert _get_ind_threshold("啤酒", "cash_to_assets_p75", 0.99) == pytest.approx(
            p70 * 1.2
        )

    def test_derived_p75_is_not_silently_the_default(self):
        """0.99 是探针: 若推算链断了, 返回的会是它"""
        got = _get_ind_threshold("啤酒", "cash_to_assets_p75", 0.99)
        assert got != pytest.approx(0.99)

    def test_no_industry_means_plain_default(self):
        """没有行业键的公司仍走通用默认值 (本轮不改变它们的行为)"""
        assert _get_ind_threshold("", "cash_to_assets_p75", 0.99) == pytest.approx(0.99)
        assert _get_ind_threshold(None, "cash_to_assets_p75", 0.99) == pytest.approx(0.99)
