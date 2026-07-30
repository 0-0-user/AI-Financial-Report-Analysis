"""
==========================================================
 llm 包 — LLM 调用统一网关
==========================================================

本包屏蔽不同 LLM 厂商的 API 差异，提供统一的调用接口。
所有层 (A0/A1/B0/D1/D2) 都通过这里调用大模型，而不是直接 new client。

这样做的好处: 
- 统一管理 API Key、重试逻辑、token 计数
- 切换厂商只需改一行配置，不需要改每层的代码
- prompt 模板统一管理，修改 prompt 不需要改代码

核心组件: 
- LLMClient:     统一的 LLM 调用接口 (支持多厂商切换) 
- PromptLoader:  从 config/prompts/ 加载和渲染 prompt 模板
- ResponseParser: 解析 LLM 输出为结构化数据
"""

from .client import LLMClient
from .prompt_loader import PromptLoader
from .response_parser import ResponseParser

__all__ = ["LLMClient", "PromptLoader", "ResponseParser"]
