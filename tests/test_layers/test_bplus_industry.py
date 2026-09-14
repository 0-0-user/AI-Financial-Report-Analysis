"""B+ 层行业阈值查找测试

接缝: resolve_industry_key(hard_tags) -> 行业阈值表里的键

B+ 用这个键去 config/industry_thresholds.yaml 取行业专属阈值
(收现比、净现比、资产负债率、母子资金分离度等)。

历史缺陷: 只认「三级」一个标签。但阈值表的键是**各层级混着**的 ——
`银行` 是一级名, `化学制药` 是二级名, `锂电池` 是三级名。只认一级的做法
在旧代码里之所以"看起来能动", 是因为旧 CSV 把三个层级全塞在了第三列里;
一旦数据按真实层级分列, 只认一级就会让大批公司查不到行业阈值。

第二个缺陷: 阈值表写的是不带罗马数字后缀的形式 (`中药`), 而申万标签带
后缀 (`中药Ⅲ`)。不做后缀剥离就永远命不中, 白丢一批行业。

本文件不碰 fallback 那段 (它另有缺陷, 会改变 83.8% 公司的阈值, 单独处理)。
"""

import csv
import io
from pathlib import Path

import pytest
import yaml

from schemas.tags import HardTag
from layers.layer_bplus_internal.logic_checks import resolve_industry_key

_ROOT = Path(__file__).resolve().parent.parent.parent
_THRESHOLDS_YAML = _ROOT / "config" / "industry_thresholds.yaml"


def _table_keys() -> set[str]:
    """直读配置文件取键集 —— 不借道被测模块的私有加载函数"""
    cfg = yaml.safe_load(_THRESHOLDS_YAML.read_text(encoding="utf-8"))
    return set(cfg.get("industries", {}))


def _tags(**levels) -> list[HardTag]:
    """按 {级别: 值} 造硬标签, 如 _tags(三级="锂电池")"""
    return [
        HardTag(system=f"申万{lv}行业", value=levels[lv])
        for lv in ("三级", "二级", "一级") if levels.get(lv)
    ]


# 变更前就能命中的行业 (以旧 CSV 的第三列去匹配)。一个都不能丢。
_KEYS_LIVE_BEFORE = {
    "化学制药", "医疗器械", "银行", "锂电池", "煤炭开采", "高速公路",
    "航运", "化妆品", "光伏发电", "生猪养殖", "啤酒", "铁路运输",
}

# 剥离罗马数字后缀后新激活的行业。旧代码命中 0 家。
_KEYS_NEWLY_ACTIVATED = {
    "中药", "IT服务", "军工电子", "证券", "航空装备", "游戏",
    "白酒", "城商行", "保险",
}


class TestResolution:
    def test_finest_matching_level_wins(self):
        assert resolve_industry_key(_tags(三级="锂电池")) == "锂电池"

    def test_level2_key_is_reachable(self):
        """二级名也是表的键 —— 只认一级的做法会漏掉它"""
        assert resolve_industry_key(_tags(二级="化学制药")) == "化学制药"

    def test_level1_key_is_reachable(self):
        """一级名也是表的键 —— 只认三级会漏掉它"""
        assert resolve_industry_key(_tags(一级="银行")) == "银行"

    def test_falls_through_to_a_coarser_level_that_matches(self):
        """最细的一级不在表里 -> 退到二级去找"""
        tags = _tags(三级="化学制剂Ⅲ", 二级="化学制药", 一级="医药生物")
        assert resolve_industry_key(tags) == "化学制药"

    def test_returns_empty_when_nothing_matches(self):
        tags = _tags(三级="光伏电池组件", 二级="光伏设备", 一级="电力设备")
        assert resolve_industry_key(tags) == ""

    def test_empty_tags_yield_empty(self):
        assert resolve_industry_key([]) == ""

    def test_blank_values_are_skipped(self):
        """空值标签不该参与匹配, 也不该把后面的候选挡住"""
        tags = [HardTag(system="申万三级行业", value="")]
        tags += _tags(一级="银行")
        assert resolve_industry_key(tags) == "银行"


class TestRomanSuffixStripping:
    @pytest.mark.parametrize("tagged,expected", [
        ("中药Ⅲ", "中药"),
        ("城商行Ⅲ", "城商行"),
        ("IT服务Ⅲ", "IT服务"),
        ("游戏Ⅲ", "游戏"),
        ("白酒Ⅲ", "白酒"),
    ])
    def test_suffix_is_stripped_to_match(self, tagged, expected):
        assert resolve_industry_key(_tags(三级=tagged)) == expected

    def test_exact_match_is_preferred_over_stripping(self):
        """表里要真有带后缀的键, 必须先命中它, 而不是剥掉再去凑"""
        exact = [k for k in _table_keys() if k.endswith(("Ⅱ", "Ⅲ"))]
        if not exact:
            pytest.skip("阈值表里没有带罗马数字后缀的键")
        for key in exact:
            assert resolve_industry_key(_tags(三级=key)) == key


_INDUSTRY_CSV = _ROOT / "data" / "industry" / "sw_industry_classification.csv"


@pytest.fixture(scope="module")
def companies() -> list[list[HardTag]]:
    """行业表里每家公司一组硬标签 —— 形态与 A1 的输出一致"""
    text = _INDUSTRY_CSV.read_bytes().decode("utf-8-sig")
    out: list[list[HardTag]] = []
    for row in csv.DictReader(io.StringIO(text)):
        tags = [
            HardTag(system=f"申万{lv}行业", value=row[f"所属申万{lv}行业"].strip())
            for lv in ("三级", "二级", "一级")
            if row[f"所属申万{lv}行业"].strip()
        ]
        if tags:
            out.append(tags)
    assert out, "行业表里没有可用数据"
    return out


class TestNoRegressionOnTheRealIndustryTable:
    """拿真实的行业表跑一遍: 原本能命中的一个不少, 后缀剥离新增的一个不落。"""

    def test_the_literal_key_sets_are_real_keys(self):
        """上面两组字面量是手抄的 —— 先钉住它们确实存在于阈值表,
        否则下面两条失败时会指向错误的方向 (像是匹配坏了, 其实是抄错了)"""
        keys = _table_keys()
        assert _KEYS_LIVE_BEFORE <= keys, sorted(_KEYS_LIVE_BEFORE - keys)
        assert _KEYS_NEWLY_ACTIVATED <= keys, sorted(_KEYS_NEWLY_ACTIVATED - keys)

    def test_previously_live_industries_are_all_still_reachable(self, companies):
        hit = {resolve_industry_key(t) for t in companies}
        missing = _KEYS_LIVE_BEFORE - hit
        assert not missing, f"这些行业变更后一家公司都命中不到了: {sorted(missing)}"

    def test_suffix_stripping_activates_the_expected_industries(self, companies):
        hit = {resolve_industry_key(t) for t in companies}
        missing = _KEYS_NEWLY_ACTIVATED - hit
        assert not missing, (
            f"这些行业本该靠剥离后缀新激活, 却一家都没命中: {sorted(missing)}"
        )

    def test_most_companies_get_some_industry_key(self, companies):
        matched = sum(1 for t in companies if resolve_industry_key(t))
        assert matched > 800, f"只有 {matched} 家公司能查到行业阈值, 明显偏少"
