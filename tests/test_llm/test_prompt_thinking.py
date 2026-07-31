"""测试 10 个 prompt 的"思考过程"指令 + 思考链剥离兼容性"""

import json

from llm.prompt_loader import PromptLoader
from llm.response_parser import ResponseParser

# 全部在用 prompt 模板（e2_report 已删除）
PROMPTS = [
    "b0_table_localization",
    "a0_search_query",
    "a0_macro_facts",
    "b0_semantic_guide",
    "d1_lookup_notes",
    "d1_hypothesis",
    "d2_merge_conflict",
    "d3_semantic_match",
    "e2_explanation",
    "e2_summary",
]


def test_all_prompts_no_explicit_thinking_instruction():
    """思考链靠模型原生 reasoning_content 截取，prompt 不再要求显式写思考过程"""
    loader = PromptLoader()
    for name in PROMPTS:
        data = loader.load(name)
        system_content = "".join(
            msg["content"] for msg in data["messages"] if msg["role"] == "system"
        )
        assert "思考过程：用中文文字" not in system_content, f"{name} 仍含显式思考指令"


def test_sample_thinking_json_parses():
    """"思考过程+JSON"输出经 split_thinking 后能被 parse_json 正常解析"""
    raw = (
        "思考过程：表头含资产、负债、期末余额，判断为资产负债表。\n\n"
        "最终结果：\n"
        '{"classifications": [{"table_id": "table_0", "statement_type": "balance_sheet"}]}'
    )
    thinking, result = ResponseParser.split_thinking(raw)
    assert thinking and "资产负债表" in thinking
    data = ResponseParser.parse_json(result)
    assert data["classifications"][0]["statement_type"] == "balance_sheet"


def test_prompt_still_renderable():
    """加了思考指令后 prompt 模板仍能正常渲染"""
    loader = PromptLoader()
    # 覆盖所有模板可能用到的变量（未用到的会被 Jinja2 忽略）
    vars_dict = {
        "tables": [{"id": "t0", "page_idx": 1, "title": "t", "header_text": "x"}],
        "business_desc": "中药", "industry_tags": "中药", "year": "2025",
        "search_results": "无", "table_headers": "期末余额",
        "indicator": "存货周转率", "actual_value": "0.1", "deviation": "偏离3倍",
        "md_text": "经营情况", "footnotes_text": "附注",
        "macro_facts": "集采", "lookups": "[]", "hypotheses": "[]",
        "cause": "行业下行", "probability": "0.3",
        "semantic_levels": [{"score_base": -3, "label": "严重", "description": "d", "keywords": ["造假"]}],
        "anomalies_json": "[]", "company_name": "测试", "stock_code": "000000",
        "report_year": 2024, "center_score": 80, "bel_score": 70, "pl_score": 90,
        "avg_uncertainty": 0.2, "anomaly_count": 3, "top_anomalies": "[]",
        "summary_data": "{}",
    }
    for name in PROMPTS:
        messages = loader.render(name, vars_dict)
        assert isinstance(messages, list) and messages
        assert all(m["role"] in ("system", "user") for m in messages)
