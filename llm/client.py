"""统一的 LLM 调用接口，屏蔽不同厂商 API 差异"""

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
