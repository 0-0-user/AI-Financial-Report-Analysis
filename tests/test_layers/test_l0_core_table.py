"""测试 L0 核心表粗筛 + 合并/母公司归桶 + 跨页合并边界"""

from layers.layer_0_extract.chunker import (
    DocumentChunker,
    _date_type_of_header,
    _is_statement_header_row,
    _looks_like_header_row,
    _merge_same_column_tables,
)


def _make_table(rows: list[list]) -> dict:
    return {"page_number": 94, "rows": rows}


def _t(page: int, rows: list[list]) -> dict:
    return {"page_number": page, "rows": rows}


# 年报三大报表的表头行（含日期数字）
BS_HEADER = ["项目", "附注", "2025年12月31日", "2024年12月31日"]


# ──────────────────────────────────────────
# 核心表粗筛 _is_core_statement_table
# ──────────────────────────────────────────

def test_core_balance_sheet():
    """资产负债表表头（含附注列 + 日期列）→ True"""
    rows = [
        ["项目", "附注", "2025年12月31日", "2024年12月31日"],
        ["货币资金", "", "100", "90"],
    ]
    assert DocumentChunker._is_core_statement_table(rows)


def test_core_income_statement():
    """利润表表头（含年度）→ True"""
    rows = [
        ["项目", "附注", "2025年度", "2024年度"],
        ["营业收入", "", "500", "450"],
    ]
    assert DocumentChunker._is_core_statement_table(rows)


def test_note_table_is_not_core():
    """附注明细表（无附注列）→ False"""
    rows = [
        ["项目", "期末余额", "期初余额"],
        ["库存现金", "100", "90"],
    ]
    assert not DocumentChunker._is_core_statement_table(rows)


def test_empty_table_is_not_core():
    assert not DocumentChunker._is_core_statement_table([])
    assert not DocumentChunker._is_core_statement_table([["项目"]])


# ──────────────────────────────────────────
# 归桶: 合并表进合并桶, 母公司表进 parent 桶
# ──────────────────────────────────────────

def test_extract_financial_data_separates_consolidated_and_parent(monkeypatch):
    chunker = DocumentChunker({"metadata": {}, "pages": []})
    tables = [
        _make_table([["项目", "附注", "2025年12月31日", "2024年12月31日"], ["资产总计", "", "100", "90"]]),
        _make_table([["项目", "附注", "2025年12月31日", "2024年12月31日"], ["资产总计", "", "50", "45"]]),
    ]
    monkeypatch.setattr(
        DocumentChunker, "_get_tables_in_range", lambda self, pr: tables,
    )
    monkeypatch.setattr(
        DocumentChunker, "_classify_tables_with_llm",
        lambda self, t: {
            "table_0": {"statement_type": "balance_sheet", "report_scope": "consolidated"},
            "table_1": {"statement_type": "balance_sheet", "report_scope": "parent"},
        },
    )

    result = chunker._extract_financial_data((94, 114))

    # 每张表 2 行（表头+数据）都归桶；合并表进合并桶、母公司表进 parent 桶
    assert len(result["balance_sheet"]) == 2
    assert len(result["parent_balance_sheet"]) == 2
    assert len(result["income_statement"]) == 0
    assert len(result["parent_income_statement"]) == 0


def test_extract_financial_data_skips_note_tables(monkeypatch):
    """附注明细表被粗筛排除，不进任何财务桶"""
    chunker = DocumentChunker({"metadata": {}, "pages": []})
    tables = [
        _make_table([["项目", "附注", "2025年12月31日", "2024年12月31日"], ["资产总计", "", "100", "90"]]),
        _make_table([["项目", "期末余额", "期初余额"], ["库存现金", "100", "90"]]),  # 附注表
    ]
    monkeypatch.setattr(
        DocumentChunker, "_get_tables_in_range", lambda self, pr: tables,
    )
    monkeypatch.setattr(
        DocumentChunker, "_classify_tables_with_llm",
        lambda self, t: {
            "table_0": {"statement_type": "balance_sheet", "report_scope": "consolidated"},
        },
    )

    result = chunker._extract_financial_data((94, 259))

    # 附注明细表未进财务桶（只有合并资产负债表的 2 行）
    assert len(result["balance_sheet"]) == 2
    assert len(result["income_statement"]) == 0
    assert len(result["parent_balance_sheet"]) == 0


def test_parse_llm_classification_with_report_scope():
    """解析 LLM 新格式（statement_type + report_scope）"""
    raw = (
        '{"classifications": ['
        '{"table_id": "table_0", "statement_type": "balance_sheet", "report_scope": "consolidated"},'
        '{"table_id": "table_1", "statement_type": "cashflow_statement", "report_scope": "parent"}]}'
    )
    result = DocumentChunker._parse_llm_classification(raw)
    assert result["table_0"] == {
        "statement_type": "balance_sheet", "report_scope": "consolidated",
    }
    assert result["table_1"]["report_scope"] == "parent"


# ──────────────────────────────────────────
# 报表边界识别 _is_statement_header_row
# ──────────────────────────────────────────

def test_statement_header_row_detects_statement_headers():
    """报表表头含'项目'+日期数字，须被认出来"""
    assert _is_statement_header_row(BS_HEADER)
    assert _is_statement_header_row(["项目", "附注", "2025年度", "2024年度"])


def test_statement_header_row_rejects_continuation_rows():
    """续页数据行不是表头"""
    assert not _is_statement_header_row(["债权投资", "七、12", "2,000,000.00", "1,500,000.00"])
    assert not _is_statement_header_row(["本期年度损益调整", "", "1,234.00", "1,000.00"])


def test_statement_header_row_differs_from_looks_like_header_row():
    """两者不可互换: 报表表头含数字, 而 _looks_like_header_row 要求不含数字"""
    assert not _looks_like_header_row(BS_HEADER)
    assert _is_statement_header_row(BS_HEADER)
    # 附注明细表表头反过来: 无日期数字
    note_header = ["项目", "重要性标准"]
    assert _looks_like_header_row(note_header)
    assert not _is_statement_header_row(note_header)


# ──────────────────────────────────────────
# 续页行不再被按关键词误判报表类型（旧 Bug A）
# ──────────────────────────────────────────

def test_date_type_of_header_ignores_continuation_rows():
    """续页数据行含"投资"/"成本"时不得猜成 q_cf/q_pl，应返回空串"""
    assert _date_type_of_header([["投资活动产生的现金流量净额", "", "1", "2"]]) == ""
    assert _date_type_of_header([["减：营业成本", "", "1", "2"]]) == ""


def test_date_type_of_header_still_guesses_for_real_headers():
    """真表头（项目+科目关键词、无日期数字）仍可按关键词判类型"""
    assert _date_type_of_header([["项目", "附注", "营业收入", "营业成本"]]) == "q_pl"
    assert _date_type_of_header([["项目", "附注", "现金及现金等价物净增加额"]]) == "q_cf"


# ──────────────────────────────────────────
# 跨页合并: 该合的合, 该切的切
# ──────────────────────────────────────────

def test_merge_joins_continuation_fragments():
    """续页首行是数据行时必须合并。

    旧版对这类行按关键词猜类型（"投资"→q_cf、"成本"→q_pl），
    与上一张表的 annual_bs 不符而拒绝合并，报表被截断成多块。
    """
    tables = [
        _t(80, [BS_HEADER, ["货币资金", "", "1", "1"]]),
        _t(81, [["投资性房地产", "", "2", "2"]]),
        _t(82, [["减：营业成本", "", "3", "3"]]),
    ]
    merged = _merge_same_column_tables(tables)

    assert len(merged) == 1
    assert len(merged[0]["rows"]) == 4
    assert merged[0]["start_page_number"] == 80
    assert merged[0]["page_number"] == 82


def test_merge_stops_at_new_statement_header():
    """新报表的首行是表头行时必须切开。

    回归: 合并资产负债表与母公司资产负债表列数相同、页码相邻、
    日期类型同为 annual_bs，仅靠类型无法区分，会把两张表粘成一张。
    """
    tables = [
        _t(123, [BS_HEADER, ["货币资金", "", "1", "1"]]),
        _t(124, [["债权投资", "", "2", "2"], ["其他权益工具", "", "3", "3"]]),
        _t(125, [BS_HEADER, ["货币资金", "", "9", "9"]]),  # 母公司BS 首页
    ]
    merged = _merge_same_column_tables(tables)

    assert len(merged) == 2, "母公司BS 不应被并入合并BS"
    assert len(merged[0]["rows"]) == 4
    assert merged[0]["start_page_number"] == 123
    assert merged[0]["page_number"] == 124
    assert len(merged[1]["rows"]) == 2
    assert merged[1]["start_page_number"] == 125


def test_merge_records_start_page_for_single_page_table():
    """未发生合并的表也要有 start_page_number"""
    merged = _merge_same_column_tables([_t(50, [BS_HEADER, ["货币资金", "", "1", "1"]])])
    assert merged[0]["start_page_number"] == 50


# ──────────────────────────────────────────
# 标题按起始页查（旧 Bug B）
# ──────────────────────────────────────────

def test_page_title_looked_up_at_start_page(monkeypatch):
    """跨页合并后标题须按起始页查。

    回归: 末页往往正是下一张报表的起始页（苏美达 p82 上同时有
    合并资产负债表的尾行和母公司资产负债表的标题），按末页查会把
    合并报表标成母公司，令 LLM 的 report_scope 判反。
    """
    pages = [
        {"page_num": 80, "text": "合并资产负债表", "tables": []},
        {"page_num": 82, "text": "母公司资产负债表", "tables": []},
    ]
    chunker = DocumentChunker({"metadata": {}, "pages": pages})
    tables = [
        _t(80, [BS_HEADER, ["货币资金", "", "1", "1"]]),
        _t(81, [["债权投资", "", "2", "2"]]),
        _t(82, [["其他权益工具", "", "3", "3"]]),   # 合并BS 尾行
        _t(82, [BS_HEADER, ["货币资金", "", "9", "9"]]),  # 母公司BS 首页
    ]
    monkeypatch.setattr(DocumentChunker, "_get_tables_in_range", lambda self, pr: tables)
    monkeypatch.setattr(
        DocumentChunker, "_classify_tables_with_llm",
        lambda self, t: {
            "table_0": {"statement_type": "balance_sheet", "report_scope": "consolidated"},
            "table_1": {"statement_type": "balance_sheet", "report_scope": "parent"},
        },
    )

    seen = []
    orig = DocumentChunker._find_page_statement_title

    def spy(self, page_num):
        seen.append(page_num)
        return orig(self, page_num)

    monkeypatch.setattr(DocumentChunker, "_find_page_statement_title", spy)

    result = chunker._extract_financial_data((80, 90))

    # 合并BS 跨 p80→p82，标题须取 p80（合并）而非 p82（母公司）
    assert seen[0] == 80, f"标题应按起始页查, 实际查了 p{seen[0]}"
    bs_names = {r["columns"].get("col_0") for r in result["balance_sheet"]}
    assert "货币资金" in bs_names
