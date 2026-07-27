"""
==========================================================
 llm/response_parser.py — LLM 输出解析器
==========================================================

大模型返回的内容通常不是完美的 JSON，可能有以下问题：
1. 用 ```json ``` 代码块包裹
2. 用单引号代替双引号
3. 多行字符串中的额外空白

本文件负责处理这些常见问题，将 LLM 的原始输出解析为
Python 可用的结构化数据。

使用方式：
    parser = ResponseParser()
    data = parser.parse_json(llm_response)
    facts = parser.extract_facts(llm_response)
    validated = parser.validate_against_schema(data, MySchema)
"""

import json
import re
from typing import Type
from pydantic import BaseModel


class ResponseParser:
    """LLM 响应解析器，处理常见格式问题"""

    @staticmethod
    def parse_json(raw: str) -> dict:
        """解析 LLM 返回的 JSON 字符串

        处理常见格式问题：
        - ```json ... ``` 包裹
        - 多余的前后空白
        - 单引号替代双引号
        """
        # 移除 markdown 代码块标记
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())

        # 尝试标准解析
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # 尝试修复单引号
        try:
            fixed = cleaned.replace("'", '"')
            return json.loads(fixed)
        except json.JSONDecodeError:
            raise ValueError(f"无法解析 LLM 输出为 JSON: {raw[:200]}")

    @staticmethod
    def extract_facts(raw: str) -> list[str]:
        """从 LLM 返回的文本中提取客观事实条目"""
        facts = []
        for line in raw.strip().split("\n"):
            line = line.strip().strip("-").strip()
            if line and len(line) > 10:
                facts.append(line)
        return facts

    @staticmethod
    def validate_against_schema(data: dict, schema: Type[BaseModel]) -> BaseModel:
        """用 Pydantic 校验并转换"""
        return schema.model_validate(data)
