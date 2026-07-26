"""LLM 响应解析器测试"""

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
