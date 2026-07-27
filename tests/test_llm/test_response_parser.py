"""
==========================================================
 tests/test_llm/test_response_parser.py — 响应解析器测试
==========================================================

测试 ResponseParser 对 LLM 输出的各种格式处理：
- 标准 JSON 解析
- Markdown 代码块包裹（```json ```）的处理
- 单引号替代双引号的容错
- 文本中提取事实条目
"""

import pytest
from llm.response_parser import ResponseParser


class TestJSONParsing:
    """JSON 解析测试"""

    def test_standard_json(self):
        """标准 JSON 应正常解析"""
        raw = '{"key": "value"}'
        result = ResponseParser.parse_json(raw)
        assert result["key"] == "value"

    def test_markdown_fenced_json(self):
        """处理 ```json``` 包裹的情况"""
        raw = '```json\n{"key": "value"}\n```'
        result = ResponseParser.parse_json(raw)
        assert result["key"] == "value"

    def test_single_quotes_json(self):
        """处理单引号替代双引号的情况"""
        raw = "{'key': 'value'}"
        result = ResponseParser.parse_json(raw)
        assert result["key"] == "value"


class TestFactsExtraction:
    """事实提取测试"""

    def test_extract_facts(self):
        """从文本中提取事实条目"""
        text = "- 事实一\n- 事实二\n- 事实三"
        facts = ResponseParser.extract_facts(text)
        assert len(facts) == 3
