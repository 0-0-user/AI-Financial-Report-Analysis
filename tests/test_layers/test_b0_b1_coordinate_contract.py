"""
==========================================================
 tests/test_layers/test_b0_b1_coordinate_contract.py
==========================================================

B0 与 B1 之间的**坐标契约**。

B0 要 LLM 回填两个坐标::

    {"location": {"row_start": 3, "col_index": 2}}

B1 把它们当真坐标用::

    rows[row_start]                      # _extract_value_fallback
    row.columns[f"col_{col_index}"]      # _extract_row_major

于是这个契约有两个义务方, 缺一不可:

  B0 的义务 —— 发给 LLM 的提示词里必须有能**推出**这两个坐标的信息。
               没给真行号, 模型只能回它看到的列表序号;
               没说 col_j 指哪一列, 模型只能猜。

  B1 的义务 —— 坐标不可信时不能把字段**静默丢掉** (fail-closed, 不是 fail-open)。

真模型实测 (2026-09-11, temperature=0, 同一提示词重复 6 次):

    2 行表头: 6/6 次 col_index 全回 0 (指到 col_0 科目名列)
    3 行表头: 2/6 次回 0, 4/6 次回 1

跑一次一个样 —— 说明这个坐标不是从表里推出来的, 是在猜。

col_index=0 的后果链:
    row.columns["col_0"] = "货币资金"
    -> _parse_number("货币资金") -> None
    -> `if value is None: continue`
    -> 字段成批消失, 报表看起来「本来就没有这一项」
"""

import json
import re
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

import layers.layer_b_extract.b0_semantic_guide as _b0_module  # noqa: E402

# b0 fixture 会把规则路径打哑, 需要真身时从这里取
_rule_guide_original = _b0_module._rule_based_guide


# ============================================================
# 合成一张行式资产负债表 —— 真值由**表的构造**决定, 与实现无关
# ============================================================
#
# 两行表头 -> 数据从 row_index=2 起。这个偏移是故意的:
# 表头只有一行时, 「字段清单里的 1-based 序号」恰好等于真行号,
# 缺陷会被掩盖 —— 这正是它至今没被测出来的原因之一。

def _row(i: int, name: str, end: str, begin: str) -> RawTableRow:
    return RawTableRow(row_index=i, page_number=1,
                       columns={"col_0": name, "col_1": end, "col_2": begin})


ROWS = [
    _row(0, "项目", "期末余额", "期初余额"),
    _row(1, "项目", "合并", "母公司"),
    _row(2, "货币资金", "1,234,567.89", "1,000,000.00"),
    _row(3, "应收账款", "  888,000.00", "  700,000.00"),
    _row(4, "存货", "  555,000.00", "  500,000.00"),
    _row(5, "固定资产", "9,999,000.00", "9,000,000.00"),
]

# 真值: 字段名 -> (真实 row_index, 期末余额)
TRUTH: dict[str, tuple[int, float]] = {
    "货币资金": (2, 1234567.89),
    "应收账款": (3, 888000.00),
    "存货": (4, 555000.00),
    "固定资产": (5, 9999000.00),
}

# 真值: col_j -> 该列的表头文字。期末余额在 col_1, 期初余额在 col_2。
VALUE_COLUMNS = {1: "期末余额", 2: "期初余额"}

# 字段名 -> 系统标准名。**断言必须用这一侧**: 报告和下流读的是 standard_name
# 那个键, 不是财报原文名。早先的版本桩把 standard_name 回成中文原名, 于是断言的
# 键和 _extract_key_totals 写入的键(英文)不相撞, 覆盖了也看不见 —— 假绿。
STANDARD = {
    "货币资金": "Monetary_Funds",
    "应收账款": "Accounts_Receivable",
    "存货": "Inventory",
    "固定资产": "Fixed_Assets",
}

# _extract_key_totals 会**无条件覆盖**这些字段 —— 它们是最容易被绕过的
KEY_TOTALS_IN_FIXTURE = {"货币资金", "存货"}


# ============================================================
# 真实表头形状 (2026-09-11 从苏美达_2025.pdf 实测)
# ============================================================
#
# 苏美达 5 张核心报表的表头**全是日期**, 一个字都不含"期末/期初":
#
#     资产负债表: 项目 | 附注 | 2025年12月31日 | 2024年12月31日
#     利 润 表  : 项目 | 附注 | 2025年度       | 2024年度
#
# 别名表认不出这些, 于是 standard_name 全空 -> _prior_period_columns 恒为空
# -> 期间守卫在真实报表上**一次都不会生效**。上面那套 期末余额/期初余额 的
# 夹具, 恰好是唯一能让它生效的布局 —— 夹具避开了真实形状。
#
# 下面这张是 3 列日期表 (省掉附注列, A 股常见变体):
# col_1 是**较新的日期** -> 本期; col_2 是较旧的 -> 上期。

def _date_row(i: int, name: str, cur: str, prior: str) -> RawTableRow:
    return RawTableRow(row_index=i, page_number=1,
                       columns={"col_0": name, "col_1": cur, "col_2": prior})


DATED_ROWS = [
    _date_row(0, "项目", "2025年12月31日", "2024年12月31日"),
    _date_row(1, "货币资金", "1,234,567.89", "1,000,000.00"),
    _date_row(2, "应收账款", "  888,000.00", "  700,000.00"),
    _date_row(3, "存货", "  555,000.00", "  500,000.00"),
    _date_row(4, "固定资产", "9,999,000.00", "9,000,000.00"),
]

# 真值同 TRUTH, 只是行号少了表头那 1 行
DATED_TRUTH: dict[str, tuple[int, float]] = {
    "货币资金": (1, 1234567.89),
    "应收账款": (2, 888000.00),
    "存货": (3, 555000.00),
    "固定资产": (4, 9999000.00),
}


def _doc_dated() -> RawDocument:
    d = _doc()
    d.financial_data.balance_sheet = list(DATED_ROWS)
    return d


def _doc() -> RawDocument:
    return RawDocument(
        metadata=DocumentMetadata(file_name="x.pdf", page_count=1),
        financial_data=FinancialTable(
            balance_sheet=list(ROWS), income_statement=[], cashflow_statement=[],
        ),
        management_discussion=ManagementDiscussion(sections=[]),
        footnotes=Footnotes(items=[]),
        company_overview=CompanyOverview(
            company_name="测试", stock_code="000001",
            business_description="", industry_classification="",
        ),
    )


# ============================================================
# 桩 LLM: 记录 B0 到底给模型看了什么
# ============================================================

class _StubLLM:
    """记录提示词, 并按类属性给定的坐标作答。

    行为只能挂在**类属性**上: `_llm_guide` 里是 `LLMClient()` 无参构造,
    构造参数传不进去。
    """

    prompt = ""
    col_index = 1          # 默认按真值作答, 好让 B0 的提示词成为唯一变量
    truth = TRUTH          # 换夹具时跟着换 (见 extract_dated)

    def __init__(self, *a, **kw):
        pass

    def chat(self, prompt_name, variables, **kw):
        type(self).prompt = variables.get("table_headers", "")
        return json.dumps({
            "columns": {"期末余额": "end_balance", "期初余额": "begin_balance"},
            "field_mapping": [
                {
                    "raw_name": name,
                    # 真模型回的是**英文标准名**。桩必须照做:
                    # 回中文原名的话, 它写的键和 _extract_key_totals 写的键
                    # 不是同一个, 覆盖了也断言不到。
                    "standard_name": STANDARD[name],
                    "location": {
                        "row_start": idx,
                        "col_index": type(self).col_index,
                    },
                    "unit": "元",
                }
                for name, (idx, _v) in type(self).truth.items()
            ],
        }, ensure_ascii=False)


def _no_rule_fallback(*a, **kw):
    """把规则降级路径打哑。

    `_process_one_table` 里 LLM 抛异常会静默降级到 `_rule_based_guide`,
    而那条路径用的是**真行号** —— 一旦降级, 屏还测得到缺陷就永远绿,
    测的根本不是被测代码。
    """
    raise AssertionError(
        "降级到规则路径了 —— LLM 路径没跑通, 本测试未覆盖被测代码")


@pytest.fixture
def b0(monkeypatch):
    """装好桩 LLM 的 b0 模块 (默认坐标全对)"""
    import llm.client
    import layers.layer_b_extract.b0_semantic_guide as _b0

    monkeypatch.setattr(llm.client, "LLMClient", _StubLLM)
    monkeypatch.setattr(_b0, "_rule_based_guide", _no_rule_fallback)
    _StubLLM.prompt = ""
    _StubLLM.col_index = 1
    _StubLLM.truth = TRUTH
    return _b0


@pytest.fixture
def guide(b0):
    """坐标全对时的 TableGuide"""
    g = b0.run_semantic_guide(_doc())
    assert g.tables, "B0 没产出任何 TableGuide"
    return g.tables[0]


@pytest.fixture
def prompt(b0) -> str:
    """B0 实际发给 LLM 的提示词"""
    g = b0.run_semantic_guide(_doc())
    assert g.tables, "B0 没产出任何 TableGuide"
    return _StubLLM.prompt


@pytest.fixture
def extract_with(b0):
    """返回调用器: col_index -> {标准名: 提取到的值}

    键是 **standard_name** —— 和报告、下流读的同一侧。

    坐标在**建 guide 之前**注入 —— 建完再改就测不到被测的那个坐标了。
    """
    def run(col_index: int) -> dict:
        _StubLLM.col_index = col_index
        guide = b0.run_semantic_guide(_doc())
        assert guide.tables, "B0 没产出任何 TableGuide"
        from layers.layer_b_extract.b1_extractors import run_extraction
        fin, _parent, _prior = run_extraction(_doc(), guide)
        return {k: v.value for k, v in fin.balance_sheet.items()}
    return run


@pytest.fixture
def dated_guide(b0):
    """日期表头 (3 列) 下的 TableGuide"""
    _StubLLM.truth = DATED_TRUTH
    g = b0.run_semantic_guide(_doc_dated())
    assert g.tables, "B0 没产出任何 TableGuide"
    return g.tables[0]


@pytest.fixture
def extract_dated(b0):
    """日期表头下: col_index -> {标准名: 值}"""
    def run(col_index: int) -> dict:
        _StubLLM.truth = DATED_TRUTH
        _StubLLM.col_index = col_index
        guide = b0.run_semantic_guide(_doc_dated())
        assert guide.tables, "B0 没产出任何 TableGuide"
        from layers.layer_b_extract.b1_extractors import run_extraction
        fin, _parent, _prior = run_extraction(_doc_dated(), guide)
        return {k: v.value for k, v in fin.balance_sheet.items()}
    return run


# ============================================================
# B0 的义务: 提示词必须能推导出它要的坐标
# ============================================================

class TestB0PromptGroundsTheCoordinates:
    """B0 要 LLM 回填坐标, 就得先把坐标的依据交出去。"""

    def test_every_field_is_listed_next_to_its_real_row_index(self, prompt):
        """字段名必须和它的**真实行号**配在一起。

        只给 `  1. 货币资金` 这种列表序号, 模型除了回那个序号别无选择 ——
        而序号 1 对应的真行号是 2。
        """
        for name, (idx, _v) in TRUTH.items():
            assert re.search(rf"行\s*{idx}\b[^\n]*{name}", prompt), (
                f"提示词里没有「行{idx} … {name}」这样的配对。"
                f"模型拿不到真行号, 只能回 1-based 列表序号, "
                f"B1 再把它当 rows 下标用就会取错行。\n"
                f"提示词实际内容:\n{prompt}"
            )

    def test_prompt_states_which_column_each_col_index_refers_to(self, prompt):
        """col_j 的语义必须写死 —— 模型无从知道 col_0 是科目名列。"""
        for j, header in VALUE_COLUMNS.items():
            line = next((ln for ln in prompt.splitlines() if f"col_{j}" in ln), "")
            assert header in line, (
                f"提示词没有说明 col_{j} 是哪一列 (应为「{header}」)。"
                f"没有列语义, col_index 就是猜的 —— "
                f"实测同一提示词重复跑会在 0 和 1 之间跳。\n"
                f"提示词实际内容:\n{prompt}"
            )


# ============================================================
# B1 的义务: 坐标不可信时不得静默丢字段
# ============================================================

class TestB1FailsClosedOnUntrustworthyCoordinate:
    """真模型高频回 col_index=0 (指到 col_0 科目名列)。"""

    def test_field_survives_col_index_pointing_at_the_name_column(
            self, extract_with):
        """col_index=0 -> col_0 是科目名 -> 解析不出数 -> 字段被 `continue` 掉。

        B1 应当识别出这一列根本不是数值列, 回退到行里真正的数值列,
        而不是把字段判成「不存在」。
        """
        got = extract_with(col_index=0)
        missing = [n for n in TRUTH if got.get(STANDARD[n]) is None]
        assert not missing, (
            f"col_index=0 导致字段被静默丢弃: {missing}。"
            f"报表里会看起来「本来就没有这一项」—— 这正是 fail-open。"
        )

    def test_values_are_not_taken_from_the_name_column(self, extract_with):
        """附表: 没被丢掉的字段, 值必须来自数值列, 不能是科目名的残渣。"""
        got = extract_with(col_index=0)
        for name, (_idx, true_val) in TRUTH.items():
            val = got.get(STANDARD[name])
            if val is None:
                continue
            assert abs(val - true_val) < 0.01, (
                f"{name}: 取到 {val}, 真值 {true_val}")


class TestB1ExtractsCorrectValuesWhenCoordinatesAreGood:
    """对照: 坐标正确时一切正常 —— 保证上面两条测的是坐标, 不是别的。"""

    def test_all_fields_extracted_with_true_values(self, extract_with):
        got = extract_with(col_index=1)
        for name, (_idx, true_val) in TRUTH.items():
            val = got.get(STANDARD[name])
            assert val is not None, f"{name} 没被提取到"
            assert abs(val - true_val) < 0.01, (
                f"{name}: 取到 {val}, 真值 {true_val}")


# ============================================================
# B0 的义务 (续) : 每列必须带上自己的列号和期间语义
# ============================================================
#
# 契约里 col_index 只是个数字, 单靠"能不能解析成数"拦不住一种错:
# 指到「期初余额」列 —— 那一列**确实**是合法数值列, 数值也解析得出,
# 于是静默把期初当期末。要拦住它, B1 必须知道"col_2 属于哪个期间",
# 而这个信息只有 B0 手里有 (表头文字)。
#
# 旧代码把 ColumnHeader.index 一律填 0, 语义也填成占位的 value_i,
# 所以下游无从校验。

class TestB0EmitsColumnSemantics:

    def test_each_column_carries_its_own_index(self, guide):
        """col_j 必须带着自己的 j —— 一律填 0 等于没有列号。"""
        indices = {c.index for c in guide.columns}
        assert set(VALUE_COLUMNS) <= indices, (
            f"guide.columns 里找不到 col_1 / col_2 的下标, 实际只有 {sorted(indices)}。"
            f"下游拿到 index=0 的一堆列, 无法回答「col_2 是哪一列」"
        )

    def test_each_column_carries_its_period_semantics(self, guide):
        """col_1 是期末、col_2 是期初 —— 这是表头里明摆着的字, 不必让 LLM 猜。"""
        by_index = {c.index: c.standard_name for c in guide.columns}
        assert by_index.get(1) == "end_balance", (
            f"col_1 是期末余额列, 语义应为 end_balance, 实际 {by_index.get(1)!r}")
        assert by_index.get(2) == "begin_balance", (
            f"col_2 是期初余额列, 语义应为 begin_balance, 实际 {by_index.get(2)!r}")

    def test_rule_path_also_carries_period_semantics(self, b0, monkeypatch):
        """降级到规则路径时语义不能丢 —— 否则两条路径取数口径不一致。"""
        monkeypatch.setattr(b0, "_rule_based_guide", _rule_guide_original)
        header_info = b0._parse_multi_level_header(ROWS, True)
        rule = b0._rule_based_guide("资产负债表", header_info, ROWS, True)
        by_index = {c.index: c.standard_name for c in rule.columns}
        assert by_index.get(1) == "end_balance"
        assert by_index.get(2) == "begin_balance"


class TestB1RejectsTheWrongPeriodColumn:
    """col_index 指到「期初余额」列: 值合法, 期间错。

    这是比"丢字段"更隐蔽的一类 fail-open —— 报表里会明明白白印着一个数,
    只是它是去年的。
    """

    @pytest.fixture
    def guide_then_extract(self, b0):
        """给一张坐标全对、但 col_index 被人为改成 2 的表"""
        def run():
            from layers.layer_b_extract.b1_extractors import run_extraction
            g = b0.run_semantic_guide(_doc())
            for fm in g.tables[0].field_mappings:
                fm.col_index = 2          # 期初余额列
            fin, _p, _prior = run_extraction(_doc(), g)
            return {k: v.value for k, v in fin.balance_sheet.items()}
        return run

    def test_period_end_values_not_silently_replaced_by_period_start(
            self, guide_then_extract):
        got = guide_then_extract()
        for name, (_idx, true_val) in TRUTH.items():
            val = got.get(STANDARD[name])
            assert val is not None, f"{name} 被丢弃"
            assert abs(val - true_val) < 0.01, (
                f"{name}: 取到 {val} —— 那是**期初余额**, "
                f"真值(期末)是 {true_val}。期间错的值比缺值更难发现。")


class TestDateColumnHeadersAreRecognised:
    """真实报表的列头是日期, 不含"期末/期初"四个字。

    苏美达_2025 实测::

        资产负债表: 项目 | 附注 | 2025年12月31日 | 2024年12月31日
        利 润 表  : 项目 | 附注 | 2025年度       | 2024年度

    别名表认不出这些。认不出的后果不是报错, 是 standard_name 全是空串 ——
    守卫看到"没有上期列"就放行, 于是**静默失效**。这比报错难查得多。
    """

    def test_dated_columns_carry_period_semantics(self, dated_guide):
        by_index = {c.index: c.standard_name for c in dated_guide.columns}
        assert by_index.get(1) == "end_balance", (
            f"col_1 是较新的日期 (2025年12月31日), 语义应为 end_balance, "
            f"实际 {by_index.get(1)!r}。日期表头认不出来, 整个期间守卫就是摆设。")
        assert by_index.get(2) == "begin_balance", (
            f"col_2 是较旧的日期 (2024年12月31日), 语义应为 begin_balance, "
            f"实际 {by_index.get(2)!r}")

    def test_key_totals_take_the_latest_date_column(self, extract_dated):
        """3 列日期表上 col_2 是**上期**。

        _extract_key_totals 写死的 col_2 优先在这里必然取到去年的数,
        而且是无条件覆盖 —— 连 B1 按坐标取对的也会被盖掉。
        """
        got = extract_dated(col_index=1)          # 坐标是对的: col_1 = 本期
        for name, (_idx, true_val) in DATED_TRUTH.items():
            val = got.get(STANDARD[name])
            assert val is not None, f"{name} 没被提取到"
            assert abs(val - true_val) < 0.01, (
                f"{name}: 取到 {val}, 真值(本期) {true_val} —— 差 {val - true_val}")

    def test_prior_date_column_never_wins_even_when_coordinate_points_at_it(
            self, extract_dated):
        """坐标指向 col_2 (上期) 时也不能照单全收 —— 它解析得出数, 只是期间不对。"""
        got = extract_dated(col_index=2)
        for name, (_idx, true_val) in DATED_TRUTH.items():
            val = got.get(STANDARD[name])
            assert val is not None, f"{name} 被丢弃"
            assert abs(val - true_val) < 0.01, (
                f"{name}: 取到 {val} —— 那是**上期**的数, 真值 {true_val}")
