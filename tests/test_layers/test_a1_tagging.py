"""A1 层硬标签测试

接缝: run_tagging(raw_doc) -> CompanyTags.hard_tags

这里是「公司 → 行业标签」的唯一入口, 下游三层都靠它:
  A2 用它挑同行池, B+ 用它查行业阈值, E2 用它标注报告。

历史缺陷: A1 会为每个级别都发一个标签, 包括值为空字符串的那些 ——
`HardTag(system="同花顺二级行业", value="")`。下游按 system 名取到空值后,
要么当成「查到了但为空」继续走, 要么静默退化成空基准。所以这里钉死两条:
  1. 标签的 system 必须说实话 (数据是申万, 不是同花顺)
  2. 值为空的级别干脆不发标签, 而不是发一个空壳
"""

import csv
import io
from pathlib import Path

import pytest

from schemas.raw_doc import (
    CompanyOverview,
    DocumentMetadata,
    FinancialTable,
    Footnotes,
    ManagementDiscussion,
    RawDocument,
)
from layers.layer_a_benchmark import a1_tagging
from layers.layer_a_benchmark.a1_tagging import run_tagging


def _doc(stock_code: str = "", company_name: str = "") -> RawDocument:
    return RawDocument(
        metadata=DocumentMetadata(file_name="x.pdf", page_count=1),
        financial_data=FinancialTable(
            balance_sheet=[], income_statement=[], cashflow_statement=[]
        ),
        management_discussion=ManagementDiscussion(sections=[]),
        footnotes=Footnotes(items=[]),
        company_overview=CompanyOverview(
            company_name=company_name or None,
            stock_code=stock_code or None,
            business_description="",
            industry_classification="",
        ),
    )


def _tags(stock_code: str, company_name: str = "") -> dict[str, str]:
    """跑一遍 A1, 把硬标签收成 {system: value} 便于断言"""
    result = run_tagging(_doc(stock_code, company_name))
    return {t.system: t.value for t in result.hard_tags}


_CSV_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "data" / "industry" / "sw_industry_classification.csv"
)


def _csv_rows() -> list[dict]:
    """直接读数据产物, 用于挑样本 —— 不碰 A1 的私有加载函数"""
    text = _CSV_PATH.read_bytes().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


@pytest.fixture(autouse=True)
def _fresh_csv_cache():
    """A1 把 CSV 缓存在模块级全局里, 跨测试会串味"""
    a1_tagging._INDUSTRY_CACHE = None
    a1_tagging._NAME_INDEX = None
    yield
    a1_tagging._INDUSTRY_CACHE = None
    a1_tagging._NAME_INDEX = None


class TestTagSystemsSayShenwan:
    def test_system_names_are_shenwan(self):
        tags = _tags("600710", "苏美达")
        assert tags, "苏美达应当能查到行业标签"
        for system in tags:
            assert "申万" in system, f"标签体系名未说实话: {system!r}"
            assert "同花顺" not in system, f"标签体系名仍写着同花顺: {system!r}"

    def test_all_three_levels_use_a_consistent_system_name(self):
        tags = _tags("300750", "宁德时代")
        assert set(tags) == {"申万一级行业", "申万二级行业", "申万三级行业"}


class TestKnownGoodChains:
    """字面量真值: 手工核对过的公司 -> 行业链。"""

    def test_full_three_level_chain(self):
        tags = _tags("300750", "宁德时代")
        assert tags == {
            "申万一级行业": "电力设备",
            "申万二级行业": "电池",
            "申万三级行业": "锂电池",
        }

    def test_commerce_and_trade_chain(self):
        """苏美达: 旧表里唯一填过一级/二级/三级的那一行"""
        tags = _tags("600710", "苏美达")
        assert tags == {
            "申万一级行业": "商贸零售",
            "申万二级行业": "贸易Ⅱ",
            "申万三级行业": "贸易Ⅲ",
        }


class TestTagsMirrorTheCsv:
    """A1 的标签必须逐字反映 CSV —— 不增、不减、不补空壳。

    CSV 是独立真值: 一行里有几列非空, A1 就该发几个标签, 而且值要一模一样。
    这条同时覆盖了「缺失的级别不发空壳」—— 少发一个空标签是它的自然推论。
    """

    @staticmethod
    def _shape(row: dict) -> int:
        return sum(
            1 for lv in ("一级", "二级", "三级")
            if row[f"所属申万{lv}行业"].strip()
        )

    def test_partial_chain_companies_exist(self):
        """样本前提: CSV 里确实存在层级不全的公司, 否则下面两条会空过。"""
        rows = _csv_rows()
        assert any(self._shape(r) == 2 for r in rows), (
            "CSV 里没有只到二级的公司 —— 这条测试失去了意义"
        )

    @pytest.mark.parametrize("levels", [2, 3])
    def test_tag_values_match_the_csv_row_exactly(self, levels):
        rows = [r for r in _csv_rows() if self._shape(r) == levels]
        assert rows, f"CSV 里没有 {levels} 级的公司"
        for row in rows[:60]:
            tags = _tags(row["股票代码"], row["股票简称"])
            expected = {
                f"申万{lv}行业": row[f"所属申万{lv}行业"].strip()
                for lv in ("一级", "二级", "三级")
                if row[f"所属申万{lv}行业"].strip()
            }
            assert tags == expected, (
                f"{row['股票简称']}({row['股票代码']}) 的标签与 CSV 不符\n"
                f"  实际: {tags}\n  应为: {expected}"
            )

    def test_no_tag_ever_has_an_empty_value(self):
        """缺失的级别必须不发标签, 而不是发一个 value="" 的空壳。

        空壳会让下游按 system 名取到空串后当成「查到了」继续走。
        """
        for row in _csv_rows()[:200]:
            for system, value in _tags(row["股票代码"], row["股票简称"]).items():
                assert value.strip(), (
                    f"{row['股票简称']} 发出了空值标签: {system}={value!r}"
                )


class TestFallbacks:
    def test_unknown_company_returns_no_tags(self):
        """查不到就返回空标签, 不做任何兜底猜测。(保持原有行为)"""
        result = run_tagging(_doc("999999", "不存在的公司"))
        assert result.hard_tags == []

    def test_falls_back_to_name_lookup(self):
        """代码缺失时按公司名模糊匹配"""
        tags = _tags("", "宁德时代")
        assert tags.get("申万三级行业") == "锂电池"

    def test_company_tags_carry_through_code_and_name(self):
        result = run_tagging(_doc("600710", "苏美达"))
        assert result.stock_code == "600710"
        assert result.company_name == "苏美达"
