"""
==========================================================
 llm/client.py — 统一的 LLM 调用接口
==========================================================

封装大模型 API 调用，提供统一的 chat() 方法。
所有需要调用 LLM 的层（A0/A1/B0/D1/D2）都使用这个 client。

核心功能：
1. 多厂商切换：支持 Anthropic / OpenAI / 本地模型
2. 统一调用：chat(prompt_name, variables) 方式
3. 结构化输出：可指定 Pydantic 模型作为输出格式
4. Token 估算：count_tokens() 用于成本监控

使用方式：
    client = LLMClient(provider="anthropic")
    result = client.chat("a1_tagging", {"company_name": "茅台", ...})

安全提醒：
    API Key 通过环境变量配置，不写入代码。
"""

from typing import Optional, Type
from pydantic import BaseModel


class LLMClient:
    """LLM 客户端封装

    支持多厂商切换（Anthropic / OpenAI / 本地模型）
    """

    def __init__(self, provider: str = "anthropic"):
        self.provider = provider
        self._client = self._init_client()

    def _init_client(self):
        """根据 provider 初始化对应 client"""
        # TODO: 实现多厂商 client 初始化
        return None

    def chat(
        self,
        prompt_name: str,
        variables: dict,
        response_schema: Optional[Type[BaseModel]] = None,
    ) -> str | BaseModel:
        """统一的 LLM 调用接口

        1. prompt_loader 加载 prompt 模板
        2. 渲染模板（填充 variables）
        3. 调用 LLM API
        4. 解析输出
        5. 若提供 schema，用 Pydantic 校验
        """
        # TODO: 实现完整调用流程
        return ""

    def count_tokens(self, text: str) -> int:
        """估算 token 用量"""
        return len(text) // 2  # 粗略估算
