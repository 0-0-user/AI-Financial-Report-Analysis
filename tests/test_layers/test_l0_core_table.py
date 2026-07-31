"""测试 L0 核心表粗筛 + 合并/母公司归桶"""

from layers.layer_0_extract.chunker import DocumentChunker


def _make_table(rows: list[list]) -> dict:
    return {"page_number": 94, "rows": rows}


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
