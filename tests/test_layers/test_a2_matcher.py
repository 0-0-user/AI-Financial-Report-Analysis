"""
==========================================================
 tests/test_layers/test_a2_matcher.py — 同行匹配测试
==========================================================

测试 A2 层同行匹配算法: 
- 硬标签应精确锁定行业范围
- 余弦相似度计算是否正确
"""

import pytest
from schemas.tags import HardTag, FinancialProfile, CompanyTags


class TestPeerMatching:
    """同行匹配测试"""

    def test_hard_tag_filter(self):
        """硬标签应精确锁定行业"""
        baijiu_tags = CompanyTags(
            company_name="茅台",
            stock_code="600519",
            hard_tags=[
                HardTag(system="申万三级行业", value="白酒Ⅲ"),
                HardTag(system="申万二级行业", value="白酒Ⅱ"),
            ],
        )
        real_estate_tags = CompanyTags(
            company_name="万科",
            stock_code="000002",
            hard_tags=[
                HardTag(system="申万三级行业", value="住宅开发"),
                HardTag(system="申万二级行业", value="房地产开发"),
            ],
        )
        # 验证硬标签不同
        baijiu_industry = [t.value for t in baijiu_tags.hard_tags]
        real_estate_industry = [t.value for t in real_estate_tags.hard_tags]
        assert baijiu_industry != real_estate_industry
        assert "白酒Ⅲ" in baijiu_industry
        assert "住宅开发" in real_estate_industry

    def test_financial_profile_numeric(self):
        """FinancialProfile 数值映射应正确"""
        fp = FinancialProfile(values=[1.0, 1.0, 0.2, 0.2, 0.2, 0.8])
        expected = [1.0, 1.0, 0.2, 0.2, 0.2, 0.8]
        assert fp.as_numeric == expected
        # as_numeric 与 values 指向同一数据
        assert fp.values == expected


# ============================================================
# 同行池必须排除目标公司自身
# ============================================================

import layers.layer_a_benchmark.a2_matcher as a2
from schemas.tags import CompanyTags, HardTag
from schemas.benchmark import MIN_PEER_SAMPLE


_COLS = ["XSMLL", "XSJLL", "TOAZZL", "ZCFZL", "YYZSRGDHBZC", "ROEJQ"]


def _year_records(base: float, years: int = 3) -> list[dict]:
    """造 n 年财务记录，6 个匹配维度都有值"""
    return [
        {**{c: base + i for c in _COLS}, "year": str(2024 - i)}
        for i in range(years)
    ]


def _tag() -> CompanyTags:
    return CompanyTags(
        company_name="苏美达", stock_code="600710",
        hard_tags=[HardTag(system="申万二级行业", value="贸易Ⅱ")],
    )


def _tag_with(levels: dict[str, str]) -> CompanyTags:
    """按 {级别: 值} 造目标公司的硬标签, 如 {"一级": "电力设备", "二级": "电池"}"""
    order = ("三级", "二级", "一级")
    return CompanyTags(
        company_name="苏美达", stock_code="600710",
        hard_tags=[
            HardTag(system=f"申万{lv}行业", value=levels[lv])
            for lv in order if levels.get(lv)
        ],
    )


def _industry_rows(rows: list[tuple]) -> list[dict]:
    """(代码, 名称, 一级, 二级, 三级) -> A2 内部的行结构"""
    return [
        {"code": c, "name": n, "level1": l1, "level2": l2, "level3": l3}
        for c, n, l1, l2, l3 in rows
    ]


def _patch_industry(monkeypatch, rows: list[tuple[str, str]]):
    """把行业 CSV 换成指定的 (代码, 名称) 列表，全部挂在 贸易Ⅱ 下"""
    monkeypatch.setattr(a2, "_INDUSTRY_CACHE", {})
    monkeypatch.setattr(a2, "_INDUSTRY_LIST", _industry_rows([
        (code, name, "", "贸易Ⅱ", "贸易Ⅲ") for code, name in rows
    ]))


def _patch_rows(monkeypatch, rows: list[tuple]):
    """把行业 CSV 换成完整的 (代码, 名称, 一级, 二级, 三级) 列表"""
    monkeypatch.setattr(a2, "_INDUSTRY_CACHE", {})
    monkeypatch.setattr(a2, "_INDUSTRY_LIST", _industry_rows(rows))


def _patch_fetch(monkeypatch, base_by_code: dict[str, float]):
    def fake(target_code, peers):
        out = {target_code: _year_records(base_by_code.get(target_code, 20.0))}
        for p in peers:
            code = p["code"]
            out[code] = _year_records(base_by_code.get(code, 30.0))
        return out
    monkeypatch.setattr(a2, "_fetch_all_financial_data", fake)


class TestPeerPoolExcludesSelf:
    """回归: 目标公司自己也在它的二级行业里，不剔除就是拿自己当基准。

    行业表只剩目标公司一行时（本项目 data/industry 的真实形态），
    池子退化成 [自己] -> 中位数 = 自己、MAD = 0，
    C 层算出 inf -> 全部指标判 extreme -> 100 分 + 高置信度。
    """

    def test_self_not_in_peer_companies(self, monkeypatch):
        _patch_industry(monkeypatch, [("600710", "苏美达"), ("600755", "厦门国贸")])
        _patch_fetch(monkeypatch, {"600710": 20.0, "600755": 40.0})

        bm, _, _ = a2.run_matching(_tag())

        codes = [p.stock_code for p in bm.peer_companies]
        assert "600710" not in codes, "目标公司出现在自己的同行列表里"
        assert codes == ["600755"]

    def test_single_row_industry_is_insufficient_not_self_compared(self, monkeypatch):
        """行业表只有目标公司一家 -> 空基准 + 显式标记样本不足"""
        _patch_industry(monkeypatch, [("600710", "苏美达")])
        _patch_fetch(monkeypatch, {"600710": 20.0})

        bm, _, _ = a2.run_matching(_tag())

        assert bm.peer_companies == []
        assert bm.peer_sample_size == 0
        assert bm.sample_sufficient is False

    def test_sample_sufficiency_is_reported_truthfully(self, monkeypatch):
        rows = [("600710", "苏美达")] + [(f"60000{i}", f"同行{i}") for i in range(6)]
        _patch_industry(monkeypatch, rows)
        _patch_fetch(monkeypatch, {code: 20.0 + i * 3 for i, (code, _n) in enumerate(rows)})

        bm, _, _ = a2.run_matching(_tag())

        assert bm.peer_sample_size == 6          # 不含目标公司自身
        assert bm.sample_sufficient is True

    def test_below_threshold_is_marked_insufficient(self, monkeypatch):
        rows = [("600710", "苏美达")] + [(f"60000{i}", f"同行{i}") for i in range(MIN_PEER_SAMPLE - 2)]
        _patch_industry(monkeypatch, rows)
        _patch_fetch(monkeypatch, {code: 20.0 + i * 3 for i, (code, _n) in enumerate(rows)})

        bm, _, _ = a2.run_matching(_tag())

        assert bm.peer_sample_size == MIN_PEER_SAMPLE - 2
        assert bm.sample_sufficient is False

    def test_target_excluded_from_normalization_stats(self, monkeypatch):
        """标准化参数只由同行样本决定 —— 目标公司不参与划定衡量自己的尺子"""
        rows = [("600710", "苏美达")] + [(f"60000{i}", f"同行{i}") for i in range(5)]
        _patch_industry(monkeypatch, rows)
        # 目标公司 base=20 -> 各年 20/21/22；同行从 25 起，两者不重叠
        _patch_fetch(monkeypatch, {code: 20.0 + i * 5 for i, (code, _n) in enumerate(rows)})

        seen: dict = {}
        real = a2._compute_robust_stats

        def spy(all_values):
            seen["values"] = [list(row) for row in all_values]
            return real(all_values)

        monkeypatch.setattr(a2, "_compute_robust_stats", spy)
        a2.run_matching(_tag())

        flat = [v for row in seen["values"] for v in row]
        assert flat, "标准化样本为空"
        assert all(v >= 25.0 for v in flat), (
            f"目标公司混进了标准化样本（最小值 {min(flat)}），"
            f"等于让它划定衡量自己的尺子"
        )


# ============================================================
# 同行池按可用层级自适应
# ============================================================

class TestAdaptivePoolLevel:
    """行业层级: 能拿到多细就用多细 (三级 > 二级 > 一级)。

    迁移后的行业表里, 5606/5615 家公司三级齐全, 少数只到二级。
    旧代码只认「二级」一个 system 名, 拿不到就返回空基准 ——
    对一个只有一级行业的公司, 横向对比直接消失。

    但取最细不等于安全: 池子会随层级变细而变小, 太小的池子在 C 层
    会被样本门槛拦下并如实标注"样本不足", 这是期望行为。
    """

    def _peers(self, level3_name: str, base: float = 30.0, n: int = 6):
        return [(f"6000{i:02d}", f"同行{i}", "电力设备", "电池", level3_name,
                 base + i * 4) for i in range(n)]

    def test_uses_level3_when_available(self, monkeypatch):
        """三级可用 -> 只拉同三级公司, 不把兄弟三级混进来"""
        rows = [("600710", "苏美达", "电力设备", "电池", "锂电池", 20.0)]
        rows += self._peers("锂电池")
        rows += self._peers("蓄电池Ⅲ", base=90.0, n=4)   # 同二级、不同三级
        _patch_rows(monkeypatch, [r[:5] for r in rows])
        _patch_fetch(monkeypatch, {r[0]: r[5] for r in rows})

        bm, _, _ = a2.run_matching(_tag_with(
            {"一级": "电力设备", "二级": "电池", "三级": "锂电池"}))

        assert bm.industry.industry_name == "锂电池"
        assert bm.industry.hard_tag_system == "申万三级行业"
        assert bm.peer_sample_size == 6, "兄弟三级公司被混进了同行池"

    def test_falls_back_to_level2_without_level3(self, monkeypatch):
        """没有三级 -> 退到二级, 此时兄弟三级应当都进池子"""
        rows = [("600710", "苏美达", "电力设备", "电池", "", 20.0)]
        rows += self._peers("锂电池")
        rows += self._peers("蓄电池Ⅲ", base=90.0, n=4)
        _patch_rows(monkeypatch, [r[:5] for r in rows])
        _patch_fetch(monkeypatch, {r[0]: r[5] for r in rows})

        bm, _, _ = a2.run_matching(_tag_with({"一级": "电力设备", "二级": "电池"}))

        assert bm.industry.industry_name == "电池"
        assert bm.industry.hard_tag_system == "申万二级行业"
        assert bm.peer_sample_size == 10

    def test_falls_back_to_level1_without_level2(self, monkeypatch):
        """只有一级 -> 用一级, 而不是直接放弃横向对比"""
        rows = [("600710", "苏美达", "电力设备", "", "", 20.0)]
        rows += self._peers("锂电池")
        rows += [("600900", "长江电力", "公用事业", "电力", "水力发电", 55.0)]
        _patch_rows(monkeypatch, [r[:5] for r in rows])
        _patch_fetch(monkeypatch, {r[0]: r[5] for r in rows})

        bm, _, _ = a2.run_matching(_tag_with({"一级": "电力设备"}))

        assert bm.industry.industry_name == "电力设备"
        assert bm.industry.hard_tag_system == "申万一级行业"
        assert bm.peer_sample_size == 6, "别的行业被混进来了"

    def test_most_specific_wins_regardless_of_tag_order(self, monkeypatch):
        """标签顺序不该影响结果 —— 挑的是级别, 不是列表位置"""
        rows = [("600710", "苏美达", "电力设备", "电池", "锂电池", 20.0)]
        rows += self._peers("锂电池")
        _patch_rows(monkeypatch, [r[:5] for r in rows])
        _patch_fetch(monkeypatch, {r[0]: r[5] for r in rows})

        shuffled = CompanyTags(
            company_name="苏美达", stock_code="600710",
            hard_tags=[
                HardTag(system="申万一级行业", value="电力设备"),
                HardTag(system="申万三级行业", value="锂电池"),
                HardTag(system="申万二级行业", value="电池"),
            ],
        )
        bm, _, _ = a2.run_matching(shuffled)
        assert bm.industry.industry_name == "锂电池"

    def test_no_industry_tags_yields_empty_benchmark(self, monkeypatch):
        """一个行业标签都没有 -> 空基准, 并且如实标成样本不足"""
        _patch_rows(monkeypatch, [("600710", "苏美达", "电力设备", "电池", "锂电池")])
        _patch_fetch(monkeypatch, {"600710": 20.0})

        bm, _, _ = a2.run_matching(_tag_with({}))

        assert bm.peer_companies == []
        assert bm.peer_sample_size == 0
        assert bm.sample_sufficient is False
