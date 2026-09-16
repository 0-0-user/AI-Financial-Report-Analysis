"""
==========================================================
 tests/test_layers/test_growth_indicator_gap.py
==========================================================

「增长类指标缺失」缺陷的回归测试。

根因: **期间被建模成"行名", 不是"列维度"。**

A 股年报的利润表没有「上期营业收入」这一行 —— 上期营收就在**同一行的
上期列**里。可 YAML (config/financial_fields.yaml:32-41) 把它定义成一个
科目行, 7 份真实报告 0 命中;  B1 全文件对 prior_amount 只有两处引用,
全是「排除」(b1_extractors.py:230 / :237), 从不当数据源。

于是所有**同比**类指标静默降级:

  Revenue_Prior_Year 缺失 -> _estimate_revenue_growth 恒 None
      -> logic_checks 「过度扩张嫌疑」「过度投资/民企扩张/销售现金背离」全灭
      -> Beneish M-Score 因 prior is None 直接返回 None (fraud_patterns:336)
      -> C 层 YYZSRGDHBZC (营收增长率) 无跨年数据可算 (mad_calculator:428)

夹具形状必须是**真实年报的形状** (2026-09-11 实测苏美达_2025.pdf):

    利润表/现金流量表: 项目 | 附注 | 2025年度       | 2024年度        (4 列, 列头是日期)
    资产负债表:        项目 | 期末 | 期初                            (3 列)

上一轮踩过的坑: 夹具用「期末余额 / 期初余额」字样, 那是别名表认得出、
日期表头认不出的布局 —— 恰好是唯一能让守卫生效的形状, 测出来是假绿。

断言必须落在**下游真正读的那一侧**: 英文标准名 (income_statement["Revenue_Prior_Year"]),
不是财报原文名 —— 桩回中文原名的话, 断言的键和被覆盖的键根本不是同一个。
"""

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from schemas.raw_doc import (  # noqa: E402
    CompanyOverview, DocumentMetadata, FinancialTable, Footnotes,
    ManagementDiscussion, RawDocument, RawTableRow,
)

REPORT_YEAR = 2025


# ============================================================
# 真年报形状的行
# ============================================================

def _row3(i: int, name: str, end: str, begin: str) -> RawTableRow:
    """3 列: 项目 | 期末 | 期初"""
    return RawTableRow(row_index=i, page_number=1,
                       columns={"col_0": name, "col_1": end, "col_2": begin})


def _row4(i: int, name: str, note: str, cur: str, prior: str) -> RawTableRow:
    """4 列: 项目 | 附注 | 本期(日期) | 上期(日期)"""
    return RawTableRow(row_index=i, page_number=1,
                       columns={"col_0": name, "col_1": note,
                                "col_2": cur, "col_3": prior})


# 资产负债表: 项目 | 期末 | 期初
BS_ROWS = [
    _row3(0, "项目", "期末", "期初"),
    _row3(1, "货币资金", "1,000,000.00", "900,000.00"),
    _row3(2, "应收账款", "2,000,000.00", "1,200,000.00"),
    _row3(3, "存货", "1,500,000.00", "1,300,000.00"),
    _row3(4, "流动资产合计", "5,000,000.00", "4,000,000.00"),
    _row3(5, "固定资产", "8,000,000.00", "7,500,000.00"),
    _row3(6, "资产总计", "13,000,000.00", "11,500,000.00"),
    _row3(7, "流动负债合计", "3,000,000.00", "2,800,000.00"),
    _row3(8, "长期借款", "1,000,000.00", "1,500,000.00"),
    _row3(9, "负债合计", "4,000,000.00", "4,300,000.00"),
    _row3(10, "所有者权益合计", "9,000,000.00", "7,200,000.00"),
    _row3(11, "未分配利润", "6,000,000.00", "5,000,000.00"),
]

# 真值 (期末, 期初) —— 由表的构造决定, 与实现无关
BS_TRUTH: dict[str, tuple[float, float]] = {
    "货币资金": (1_000_000.00, 900_000.00),
    "应收账款": (2_000_000.00, 1_200_000.00),
    "存货": (1_500_000.00, 1_300_000.00),
    "流动资产合计": (5_000_000.00, 4_000_000.00),
    "固定资产": (8_000_000.00, 7_500_000.00),
    "资产总计": (13_000_000.00, 11_500_000.00),
    "流动负债合计": (3_000_000.00, 2_800_000.00),
    "长期借款": (1_000_000.00, 1_500_000.00),
    "负债合计": (4_000_000.00, 4_300_000.00),
    "所有者权益合计": (9_000_000.00, 7_200_000.00),
    "未分配利润": (6_000_000.00, 5_000_000.00),
}


def _pl_rows(prior_revenue: str = "8,000,000.00") -> list[RawTableRow]:
    """利润表: 项目 | 附注 | 2025年度 | 2024年度"""
    return [
        _row4(0, "项目", "附注", "2025年度", "2024年度"),
        _row4(1, "营业收入", "七、1", "10,000,000.00", prior_revenue),
        _row4(2, "营业成本", "七、2", "6,000,000.00", "5,000,000.00"),
        _row4(3, "销售费用", "七、3", "500,000.00", "400,000.00"),
        _row4(4, "净利润", "七、4", "2,000,000.00", "1,600,000.00"),
    ]


# 真值: 本期 10,000,000.00, 上期 8,000,000.00
PL_REVENUE_CURRENT = 10_000_000.00
PL_REVENUE_PRIOR = 8_000_000.00

# 现金流量表: 项目 | 期末 | 期初
CF_ROWS = [
    _row3(0, "项目", "期末", "期初"),
    _row3(1, "经营活动产生的现金流量净额", "1,500,000.00", "1,300,000.00"),
]


def _doc(pl_rows: list[RawTableRow] | None = None,
         bs_rows: list[RawTableRow] | None = None) -> RawDocument:
    return RawDocument(
        metadata=DocumentMetadata(file_name="测试年报.pdf", page_count=1,
                                  report_year=REPORT_YEAR),
        financial_data=FinancialTable(
            balance_sheet=list(bs_rows if bs_rows is not None else BS_ROWS),
            income_statement=list(pl_rows if pl_rows is not None else _pl_rows()),
            cashflow_statement=list(CF_ROWS),
        ),
        management_discussion=ManagementDiscussion(sections=[]),
        footnotes=Footnotes(items=[]),
        company_overview=CompanyOverview(
            company_name="测试公司", stock_code="000001",
            business_description="", industry_classification="",
        ),
    )


# ============================================================
# 真 B0 产出的 guide (期间语义不是桩自造的)
# ============================================================

@pytest.fixture
def b0(monkeypatch):
    """打哑 LLM 路径, 走规则路径。

    被测对象是 **B1 的取列逻辑**; B0 在这里只负责交出一份带期间语义的
    guide。而期间语义两条路径都出自同一个 `_column_headers` ——
    列头日期 -> end_balance / begin_balance, 与 B1 的守卫同源。
    """
    import layers.layer_b_extract.b0_semantic_guide as _b0

    def _no_llm(*a, **kw):
        raise RuntimeError("本测试不打真模型: 请走规则路径")

    monkeypatch.setattr(_b0, "_llm_guide", _no_llm)
    return _b0


def _guide(doc: RawDocument, b0):
    g = b0.run_semantic_guide(doc)
    assert len(g.tables) == 3, (
        f"B0 只识别出 {len(g.tables)} 张表: {[t.table_name for t in g.tables]}")
    return g


def _extract(doc: RawDocument, b0):
    """B1 的公开入口: run_extraction -> (合并, 母公司, 上期)"""
    from layers.layer_b_extract.b1_extractors import run_extraction
    return run_extraction(doc, _guide(doc, b0))


# ============================================================
# Tier-1: 上期营收来自**上期列**, 不是「上期营业收入」这一行
# ============================================================

class TestPriorRevenueComesFromThePriorColumn:
    """A 股利润表没有「上期营业收入」这个科目行 —— 上期营收在同一行的上期列。

    苏美达_2025 实测: 列头是 2025年度 / 2024年度, 一个字都不含"上期"。
    """

    def test_revenue_prior_year_is_read_from_the_prior_period_column(self, b0):
        financials, _parent, _prior = _extract(_doc(), b0)

        current = financials.income_statement.get("Revenue_Total")
        assert current is not None, "本期营业收入都没提取到, 夹具或 B0 出问题了"
        assert current.value == pytest.approx(PL_REVENUE_CURRENT)

        prior = financials.income_statement.get("Revenue_Prior_Year")
        assert prior is not None, (
            "Revenue_Prior_Year 缺失 —— 上期营收在**上期列**(2024年度)里, "
            "不是「上期营业收入」这一行。缺了它, 同比增速 (logic_checks "
            "_estimate_revenue_growth)、营收增长率(YYSZRGDHBZC)、Beneish "
            "M-Score 全部静默降级。\n"
            f"income_statement 实有字段: {sorted(financials.income_statement)}")
        assert prior.value == pytest.approx(PL_REVENUE_PRIOR), (
            f"上期营收取到 {prior.value}, 真值(上期列 2024年度) {PL_REVENUE_PRIOR}")

    def test_unparseable_prior_cell_is_never_written(self, b0):
        """上期格存在但解析不出数 (真实报表里是横杠占位) —— 宁缺勿错。

        硬写成 0 或把「—」当 0, 同比就成了 +∞ / +100%, 下游 (过度扩张、
        销售现金背离) 会当成"暴增"报出来。缺值至少是诚实的。
        """
        financials, _parent, _prior = _extract(_doc(pl_rows=_pl_rows(prior_revenue="—")), b0)

        assert financials.income_statement["Revenue_Total"].value == pytest.approx(
            PL_REVENUE_CURRENT), "本期营收不该受上期格影响"
        assert "Revenue_Prior_Year" not in financials.income_statement, (
            "上期格是「—」, 解析不出数, 却仍然写进了一个值: "
            f"{financials.income_statement.get('Revenue_Prior_Year')}")


# ============================================================
# Tier-1: 期初未分配利润 = 「未分配利润」行的**上期列**
# ============================================================

class TestRetainedEarningsBeginComesFromThePriorColumn:
    """config/financial_fields.yaml:189-194 把期初未分配利润定义成**一个科目行**
    (期初未分配利润 / 年初未分配利润) —— A 股资产负债表里没有这两行。

    后果不是缺一个字段而已: b1_validators.py:91-96 拿不到它就把
    「未分配利润勾稽」判成 `passed=True` + detail "跳过" —— **伪装成校验通过**。
    """

    def test_begin_is_read_from_the_prior_period_column_of_the_row(self, b0):
        financials, _parent, _prior = _extract(_doc(), b0)

        end = financials.balance_sheet.get("Retained_Earnings_End")
        assert end is not None, "期末未分配利润都没提取到, 夹具或 B0 出问题了"
        assert end.value == pytest.approx(BS_TRUTH["未分配利润"][0]), (
            "期末未分配利润被取成了别的列/别的行")

        begin = financials.balance_sheet.get("Retained_Earnings_Begin")
        assert begin is not None, (
            "Retained_Earnings_Begin 缺失 —— 它的值就是「未分配利润」行的"
            "**期初列**, 不是「期初未分配利润」这一行 (报表里没有这一行)。\n"
            f"balance_sheet 实有字段: {sorted(financials.balance_sheet)}")
        assert begin.value == pytest.approx(BS_TRUTH["未分配利润"][1]), (
            f"期初未分配利润取到 {begin.value}, 真值(期初列) {BS_TRUTH['未分配利润'][1]}")


# ============================================================
# Tier-2: B1 额外产出一份**上期报表**
# ============================================================

class TestB1AlsoEmitsThePriorPeriodStatement:
    """B+ 的 Beneish M-Score 要的是**整份上期报表** —— 12 个字段:
    应收/销售/成本/流动资产/固定资产/总资产/折旧/销管费用/流动负债/
    长期债务/净利润/货币资金 (fraud_patterns.py:354-366)。
    只补一个营收同比 (Tier-1) 救不了它。

    上期报表不是 parent_financials (那是**同一期**的母公司报表),
    也不是 multi_year_financials (那是东财同行多年度数据)。
    """

    def test_extraction_returns_the_prior_period_statement(self, b0):
        current, _parent, prior = _extract(_doc(), b0)

        assert prior.income_statement["Revenue_Total"].value == pytest.approx(
            PL_REVENUE_PRIOR), "上期报表的营业收入该是上期列的数"
        assert prior.balance_sheet["Total_Assets"].value == pytest.approx(
            BS_TRUTH["资产总计"][1])
        assert prior.balance_sheet["Inventory"].value == pytest.approx(
            BS_TRUTH["存货"][1])
        assert prior.cashflow["Cash_Flow_Op"].value == pytest.approx(1_300_000.00)

        # 当期那一份不许被上期污染
        assert current.balance_sheet["Inventory"].value == pytest.approx(
            BS_TRUTH["存货"][0])
        assert current.cashflow["Cash_Flow_Op"].value == pytest.approx(1_500_000.00)


# ============================================================
# Tier-2: 上期报表挂到 Context 后, B+ 的 Beneish M-Score 才活
# ============================================================

# 一份**典型操纵画像**的当期/上期数 —— 手工给定, 与实现无关:
# 销售暴增 (SGI 1.67) + 应收比销售涨得更快 (DSRI 1.6) + 毛利率下滑
# (GMI 1.22) + 资产变"重"/非流动占比升高 (AQI 2.67) + 巨额应计
# (TATA 0.22, 净利 120 而经营现金流 -100)。Beneish (1999) 原文里
# 这类组合就是该被标记的。
_CURRENT_FIELDS = {
    "Accounts_Receivable": 400.0, "Current_Assets": 600.0, "PPE_Net": 300.0,
    "Total_Assets": 1000.0, "Current_Liabilities": 300.0, "Long_Term_Debt": 100.0,
    "Revenue_Total": 1000.0, "Cost_Revenue": 700.0, "Depreciation": 30.0,
    "SGA_Expense": 60.0, "Net_Profit": 120.0, "Operating_Cash_Flow": -100.0,
}
_PRIOR_FIELDS = {
    "Accounts_Receivable": 150.0, "Current_Assets": 520.0, "PPE_Net": 250.0,
    "Total_Assets": 800.0, "Current_Liabilities": 200.0, "Long_Term_Debt": 80.0,
    "Revenue_Total": 600.0, "Cost_Revenue": 380.0, "Depreciation": 25.0,
    "SGA_Expense": 40.0, "Net_Profit": 60.0, "Operating_Cash_Flow": 20.0,
}

_BS_KEYS = ("Accounts_Receivable", "Current_Assets", "PPE_Net", "Total_Assets",
            "Current_Liabilities", "Long_Term_Debt")
_PL_KEYS = ("Revenue_Total", "Cost_Revenue", "Depreciation", "SGA_Expense",
            "Net_Profit")


def _statement(values: dict[str, float], year: int, report_type: str):
    from schemas.financial import FinancialStatement, FinancialField, ValidationResult
    mk = lambda keys: {  # noqa: E731
        k: FinancialField(standard_name=k, raw_name=k, value=values[k],
                          original_unit="元", report_type=report_type)
        for k in keys
    }
    return FinancialStatement(
        company_name="测试公司", stock_code="000001", year=year,
        report_type=report_type,
        balance_sheet=mk(_BS_KEYS), income_statement=mk(_PL_KEYS),
        cashflow=mk(("Operating_Cash_Flow",)),
        validation=ValidationResult(is_valid=True, checks=[]),
    )


class TestBplusScoresEarningsManipulationFromThePriorStatement:
    """fraud_patterns.run_extended_checks 早就接受 `prior=`, 但
    layers/layer_bplus_internal/__init__.py 只传了 ctx.financials ——
    `prior` 永远是默认的 None, M-Score 于是恒不运行 (symptom 3)。
    """

    def _ctx(self):
        from pipeline.context import PipelineContext
        from schemas.tags import CompanyTags
        ctx = PipelineContext()
        ctx.tags = CompanyTags(company_name="测试公司", stock_code="000001",
                               hard_tags=[])
        ctx.validation_passed = True
        ctx.financials = _statement(_CURRENT_FIELDS, 2025, "合并报表")
        ctx.prior_financials = _statement(_PRIOR_FIELDS, 2024, "上期合并报表")
        return ctx

    def test_m_score_anomaly_appears_when_the_prior_statement_is_present(self):
        from layers.layer_bplus_internal import run as run_bplus
        ctx = self._ctx()

        run_bplus(ctx)

        names = [a.check_name for a in ctx.logic_anomalies]
        assert "M-Score盈余操纵" in names, (
            "B+ 跑完了却没有 M-Score 异常 —— ctx.prior_financials 没有被传给 "
            "run_extended_checks, prior 仍是 None, M-Score 静默不执行。 "
            f"实有异常: {names}")

    def test_m_score_stays_silent_without_a_prior_statement(self):
        """缺上期报表时**不许**凭空算 M-Score —— 没有比较基数却报"操纵",
        比不报更糟。这条守卫让上面那条测试的绿是有意义的绿。
        """
        from layers.layer_bplus_internal import run as run_bplus
        ctx = self._ctx()
        ctx.prior_financials = None

        run_bplus(ctx)

        names = [a.check_name for a in ctx.logic_anomalies]
        assert "M-Score盈余操纵" not in names, (
            f"没有上期数据却报了 M-Score: {names}")


# ============================================================
# Tier-2: C 层拿到公司自己的历史序列, Pettitt 变点检测才活
# ============================================================

def _benchmark(peer_median: dict[str, float]):
    from schemas.benchmark import Benchmark, IndustryProfile
    return Benchmark(
        industry=IndustryProfile(industry_name="测试行业"),
        peer_median=peer_median, historical_mean={}, peer_companies=[],
        peer_sample_size=0, sample_sufficient=False,
    )


# 8 年毛利率: 前四年 30%, 后四年 10% —— 教科书式的台阶式突变。
# Pettitt 在这种干净台阶上 p<0.001; 抖动版本 p 会逼近 1, 测不出来。
_GROSS_MARGIN_STEP = [30.0, 30.0, 30.0, 30.0, 10.0, 10.0, 10.0, 10.0]


class TestDeviationUsesTheCompanyOwnHistory:
    """mad_calculator 里 Pettitt 那段 (`if historical:` 分支) 是**死代码**:
    它拿 benchmark 的键 (东财代码, 如 XSMLL) 去 `_get_field_value` 里查
    英文标准名字段, 永远查不到 -> 每个指标都 `continue`。
    """

    def test_historical_series_produces_a_temporal_change_point(self):
        from layers.layer_c_deviation.mad_calculator import run_deviation_analysis

        fin = _statement(_CURRENT_FIELDS, 2025, "合并报表")
        deviations = run_deviation_analysis(
            fin, _benchmark({"XSMLL": 30.0}),
            historical={"XSMLL": list(_GROSS_MARGIN_STEP)},
        )

        names = [d.indicator for d in deviations]
        assert any("(时序突变)" in n for n in names), (
            "给了 8 年台阶式历史序列, 却没有报出时序突变 —— "
            "historical 分支里 actual 是用东财代码 XSMLL 去查英文字段, "
            "查不到就 continue 了。 "
            f"实有偏离: {names}")

    def test_revenue_growth_rate_is_computed_from_the_prior_year_revenue(self):
        """mad_calculator:428 原话: 「YYZSRGDHBZC (营收增长率) 需要跨年数据，暂跳过」。

        跨年数据现在就在同一份报表里 (Revenue_Prior_Year, Tier-1 补的),
        所以这个指标不再需要跳过 —— 否则 A2 明明把它放进了 peer_median,
        C 层却永远静默地少判一个指标。
        """
        from layers.layer_c_deviation.mad_calculator import run_deviation_analysis
        from schemas.benchmark import PeerCompany

        fin = _statement(_CURRENT_FIELDS, 2025, "合并报表")
        fin.income_statement["Revenue_Prior_Year"] = type(
            fin.income_statement["Revenue_Total"])(
            standard_name="Revenue_Prior_Year", raw_name="上期营业收入",
            value=600.0, original_unit="元", report_type="合并报表")

        bm = _benchmark({"YYZSRGDHBZC": 11.0})
        bm.peer_companies = [
            PeerCompany(name=f"同行{i}", stock_code=f"00000{i}",
                        similarity_score=0.9, financials={"YYZSRGDHBZC": v})
            for i, v in enumerate([10.0, 12.0, 11.0, 13.0, 9.0])
        ]

        deviations = run_deviation_analysis(fin, bm)

        hit = [d for d in deviations if d.indicator == "营业收入同比增长率"]
        assert hit, (
            "营收增长率没有算出来 —— 本期的 Revenue_Total=1000 与 "
            "Revenue_Prior_Year=600 都在报表里, 增长率 = 66.7%。 "
            f"实有偏离: {[d.indicator for d in deviations]}")
        assert hit[0].actual_value == pytest.approx(66.6667, abs=1e-3)

class TestLayerCPassesTheHistoryDown:
    """C 层入口 `run(ctx)` 只传了 (financials, benchmark), `historical`
    永远是默认的 None —— 于是 Pettitt 那段判不了任何东西, 无论有多少历史。
    """

    def _ctx(self, rows):
        from pipeline.context import PipelineContext
        from schemas.tags import CompanyTags
        ctx = PipelineContext()
        ctx.tags = CompanyTags(company_name="测试公司", stock_code="000001",
                               hard_tags=[])
        ctx.financials = _statement(_CURRENT_FIELDS, 2025, "合并报表")
        ctx.benchmark = _benchmark({"XSMLL": 30.0})
        ctx.multi_year_financials = {"000001": rows}
        return ctx

    def test_layer_c_entry_passes_the_company_history_to_the_analyzer(self):
        from layers.layer_c_deviation import run as run_c

        # A2 切出来的 multi_year_financials 是**年份降序**的原始行
        years = [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]
        rows = [
            {"year": str(y), "XSMLL": v, "REPORT_DATE": f"{y}-12-31"}
            for y, v in sorted(zip(years, _GROSS_MARGIN_STEP), reverse=True)
        ]
        ctx = self._ctx(rows)

        run_c(ctx)

        names = [d.indicator for d in ctx.deviations]
        assert any("(时序突变)" in n for n in names), (
            "C 层入口没把 ctx.multi_year_financials 传给 run_deviation_analysis —— "
            "Pettitt 变点检测拿不到序列, 永远跑不到。 "
            f"实有偏离: {names}")

class TestLayerBMountsThePriorStatementOnTheContext:
    """B1 交出三份表只是第一步 —— 挂在 PipelineContext 上的那根线也得连上,
    否则 B+ / C 拿到的还是 None。这条测的是**接口的接缝**, 不是 B1 内部。
    """

    def test_layer_b_entry_sets_ctx_prior_financials(self, b0):
        from pipeline.context import PipelineContext
        from layers.layer_b_extract import run as run_b

        ctx = PipelineContext()
        ctx.raw_doc = _doc()

        run_b(ctx)

        assert ctx.prior_financials is not None, (
            "B 层跑完 ctx.prior_financials 还是 None —— B1 的第三份返回值没被挂上去。")
        assert ctx.prior_financials.income_statement[
            "Revenue_Total"].value == pytest.approx(PL_REVENUE_PRIOR)
        # 当期那一份不许被上期污染
        assert ctx.financials.income_statement[
            "Revenue_Total"].value == pytest.approx(PL_REVENUE_CURRENT)
