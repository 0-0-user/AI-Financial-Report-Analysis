"""
==========================================================
 llm/client.py — 统一的 LLM 调用接口 (多厂商支持) 
==========================================================

本文件是所有 AI 分析层的唯一 LLM 入口。任何一层要调大模型，
都必须通过这里的 LLMClient，而不是直接 new 各厂商的 SDK。

这样做的好处: 
- 切换厂商: 改环境变量 LLM_PROVIDER 即可，不需要改每层代码
- 统一重试: 网络错误 / 限流 / 超时，这里统一处理
- 统一解析: 自动处理 JSON fence、单引号等格式问题
- 结构化输出: 传入 Pydantic schema 即可自动校验

厂商支持 (通过环境变量 LLM_PROVIDER 切换) : 
- anthropic — Claude Sonnet / Opus (默认) 
- openai   — GPT-4o / GPT-4o-mini
- deepseek — DeepSeek-V3 / DeepSeek-R1
- qwen     — 通义千问 Qwen-Plus / Qwen-Max
- glm      — 智谱 GLM-4-Plus
- moonshot — Moonshot / Kimi
- gemini   — Google Gemini 2.0 Flash
- doubao   — 字节豆包 Pro-32K

使用方式: 
    client = LLMClient(provider="anthropic")
    # 自由文本输出
    result = client.chat("a1_tagging", {"company": "茅台"})
    # 结构化输出 (自动校验) 
    result = client.chat("b0_semantic_guide", {...}, response_schema=B0Guide)

环境变量配置 (二选一即可) : 
    方案 A — Anthropic: ANTHROPIC_API_KEY=xxx
    方案 B — OpenAI 兼容: LLM_PROVIDER=deepseek 且 DEEPSEEK_API_KEY=xxx
"""

import os
import time
import logging
from pathlib import Path
from typing import Optional, Type

from pydantic import BaseModel

from llm.prompt_loader import PromptLoader
from llm.response_parser import ResponseParser
from pipeline.tracer import tracer

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 自动加载 .env 文件 (项目根目录) 
# ──────────────────────────────────────────────
def _load_env() -> None:
    """从项目根目录加载 .env 文件 (如不存在则静默跳过) """
    project_root = Path(__file__).resolve().parent.parent  # llm/ -> 项目根
    env_path = project_root / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            loaded = load_dotenv(env_path, override=True)
            if loaded:
                logger.info("已加载 .env 配置文件: %s", env_path)
            else:
                logger.debug(".env 文件为空或全部已存在: %s", env_path)
        except ImportError:
            logger.warning("python-dotenv 未安装，跳过 .env 加载")
    else:
        logger.debug("不存在 .env 文件，跳过: %s", env_path)


_load_env()


# ──────────────────────────────────────────────
# 厂商配置表
# ──────────────────────────────────────────────
# adapter="anthropic" -> 用 anthropic SDK
# adapter="openai"    -> 用 openai SDK (兼容绝大多数厂商) 
# env_key: 从哪个环境变量读取 API Key

PROVIDER_CONFIG: dict[str, dict] = {
    "anthropic": {
        "adapter": "anthropic",
        "env_key": "ANTHROPIC_API_KEY",
        "default_model": "claude-sonnet-5-20250610",
    },
    "openai": {
        "adapter": "openai",
        "env_key": "OPENAI_API_KEY",
        "base_url": None,
        "default_model": "gpt-4o-2025-06-01",
    },
    "deepseek": {
        "adapter": "openai",
        "env_key": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-v4-flash",
        "max_output_tokens": 16384,
    },
    "qwen": {
        "adapter": "openai",
        "env_key": "QWEN_API_KEY",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
    },
    "glm": {
        "adapter": "openai",
        "env_key": "GLM_API_KEY",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        "default_model": "glm-4.7-flash",
        "max_output_tokens": 16384,
    },
    "moonshot": {
        "adapter": "openai",
        "env_key": "MOONSHOT_API_KEY",
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-8k",
    },
    "gemini": {
        "adapter": "openai",
        "env_key": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-2.0-flash",
    },
    "doubao": {
        "adapter": "openai",
        "env_key": "DOUBAO_API_KEY",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "default_model": "doubao-pro-32k",
    },
}

# 机械映射类任务：原生思考链价值低且大幅拖慢/不稳定（实测 B0 字段映射可卡数小时），
# 对这些 prompt 单独关闭 thinking；推理类任务（L0分类/D1/D2/D3/E2）保留原生思考链。
NO_THINKING_PROMPTS: set[str] = {"b0_semantic_guide"}

# prompt 名 → 层归属（用于日志归类）
PROMPT_TO_LAYER: dict[str, str] = {
    "b0_table_localization": "L0",
    "a0_search_query": "A0",
    "a0_macro_facts": "A0",
    "b0_semantic_guide": "B0",
    "d1_lookup_notes": "D1路1",
    "d1_hypothesis": "D1路2",
    "d2_merge_conflict": "D2",
    "d3_semantic_match": "D3",
    "e2_explanation": "E2",
    "e2_summary": "E2",
}


def _messages_to_text(messages: list[dict]) -> str:
    """把渲染后的消息列表拼成可读文本（供日志输入摘要）"""
    parts = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(str(c) for c in content if isinstance(c, (str, dict)))
        parts.append(f"[{role}] {content}")
    return "\n".join(parts)


class LLMClient:
    """统一的 LLM 客户端，屏蔽多厂商差异"""

    def __init__(
        self,
        provider: str = "",
        model: Optional[str] = None,
        max_retries: int = 3,
        timeout_seconds: int = 300,
    ):
        """
        Args:
            provider: 厂商名 (见 PROVIDER_CONFIG) ，空字符从 LLM_PROVIDER 读取
            model: 模型名称，不传则使用各厂商默认模型
            max_retries: API 调用失败时的最大重试次数
            timeout_seconds: 单次 API 调用的超时时间
        """
        if not provider:
            provider = os.getenv("LLM_PROVIDER", "anthropic")

        config = PROVIDER_CONFIG.get(provider)
        if not config:
            raise ValueError(
                f"不支持的 LLM 厂商: {provider}，可选: {list(PROVIDER_CONFIG.keys())}"
            )

        self.provider = provider
        self.config = config
        # 模型：界面可在 LLM_MODEL_{PROVIDER} 环境变量指定（如 LLM_MODEL_DEEPSEEK），否则用厂商默认。
        # 用"LLM_MODEL_"前缀避免与系统其他 {PROVIDER}_MODEL 环境变量冲突。
        self.model = (
            model
            or os.getenv(f"LLM_MODEL_{provider.upper()}")
            or config["default_model"]
        )
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds

        self._prompt_loader = PromptLoader()
        self._response_parser = ResponseParser()
        self._client = self._init_client()

    # ──────────────────────────────────────────────
    # 初始化各厂商 SDK
    # ──────────────────────────────────────────────

    def _init_client(self):
        """根据 provider 配置初始化对应的 SDK 客户端"""
        adapter = self.config["adapter"]
        if adapter == "anthropic":
            return self._init_anthropic()
        elif adapter == "openai":
            return self._init_openai()
        return None

    def _read_api_key(self) -> str:
        """从环境变量读取 API Key"""
        env_key = self.config["env_key"]
        api_key = os.getenv(env_key, "")
        if not api_key:
            logger.warning("%s 未设置，使用占位 key (API 调用会失败) ", env_key)
            api_key = "sk-placeholder"
        return api_key

    def _init_anthropic(self):
        """初始化 Anthropic SDK"""
        api_key = self._read_api_key()
        try:
            import anthropic
            return anthropic.Anthropic(
                api_key=api_key,
                max_retries=self.max_retries,
                timeout=self.timeout_seconds,
            )
        except ImportError:
            logger.error("anthropic SDK 未安装，请执行: pip install anthropic")
            return None

    def _init_openai(self):
        """初始化 OpenAI 兼容 SDK (DeepSeek / Qwen / GLM / Moonshot 等) """
        api_key = self._read_api_key()
        base_url = self.config.get("base_url")
        try:
            from openai import OpenAI
            return OpenAI(
                api_key=api_key,
                base_url=base_url,
                max_retries=self.max_retries,
                timeout=self.timeout_seconds,
            )
        except ImportError:
            logger.error("openai SDK 未安装，请执行: pip install openai")
            return None

    # ──────────────────────────────────────────────
    # 核心调用方法
    # ──────────────────────────────────────────────

    def chat(
        self,
        prompt_name: str,
        variables: dict,
        response_schema: Optional[Type[BaseModel]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str | BaseModel:
        """统一的 LLM 调用接口

        流程:
        1. PromptLoader 加载并渲染模板
        2. 调用各厂商 API (自动重试) 
        3. ResponseParser 解析输出
        4. 若提供 schema，用 Pydantic 校验后返回结构化对象

        Args:
            prompt_name: 模板文件名 (不含 .yaml) ，如 "a1_tagging"
            variables: 模板变量字典，如 {"company_name": "茅台"}
            response_schema: 可选的 Pydantic 模型，用于结构化输出
            temperature: 采样温度 (None 代表使用模型默认值) 
            max_tokens: 最大输出 token 数 (None 代表使用模型默认值) 

        Returns:
            - 不传 response_schema: 返回原始响应文本 (str)
            - 传 response_schema: 返回校验后的 Pydantic 模型实例
        """
        # 步骤1: 加载并渲染 prompt
        prompt_data = self._prompt_loader.load(prompt_name)
        messages = self._prompt_loader.render(prompt_name, variables)
        # 从 prompt 配置中读 temperature/max_tokens (调用层传参优先)
        if temperature is None:
            temperature = prompt_data.get("temperature")
        if max_tokens is None:
            max_tokens = prompt_data.get("max_tokens")

        # max_tokens 上限保护: 不超过模型输出上限，避免 API 报错
        max_output = self.config.get("max_output_tokens")
        if max_output and (max_tokens is None or max_tokens > max_output):
            max_tokens = max_output

        # 步骤2: 调用 API (带重试)，自动记录到流水账
        layer = PROMPT_TO_LAYER.get(prompt_name, "")
        self._active_prompt = prompt_name  # 供 _call_openai 决定是否关闭原生思考
        tracer.llm_start(prompt_name, layer, self.model, _messages_to_text(messages))
        start = time.time()

        # 调用 + 空输出重试: 原生思考链可能占用全部 max_tokens 导致 content 为空，
        # 这是模型行为不稳定而非请求失败——重试至多 3 次，仍空则终止（不做降级）。
        raw_result = None
        empty_retries = 0
        while True:
            try:
                raw_result = self._call_with_retry(
                    messages, temperature=temperature, max_tokens=max_tokens,
                )
            except Exception as e:
                tracer.llm_fail(error=str(e), duration_ms=(time.time() - start) * 1000)
                raise

            content = raw_result.get("content") or ""
            if content.strip():
                break
            empty_retries += 1
            if empty_retries >= 3:
                raise RuntimeError("LLM 连续 3 次输出为空（原生思考链占用全部输出预算）")
            logger.warning(f"LLM 输出为空（第 {empty_retries} 次），重试...")
            tracer.llm_fail(error="输出为空", duration_ms=(time.time() - start) * 1000)

        # 步骤3: 剥离"思考过程"，拿到纯结果（模型原生思考链已在流式中记录）
        explicit_thinking, result_text = ResponseParser.split_thinking(content)
        if explicit_thinking is None:
            result_text = content  # 模型未按格式输出，原样当结果

        # 步骤4: 解析输出 + 记账
        duration_ms = (time.time() - start) * 1000
        if response_schema is not None:
            parsed = self._parse_structured(result_text, response_schema)
            tracer.llm_done(
                parsed_preview=str(parsed)[:800],
                duration_ms=duration_ms,
                tokens_input=raw_result.get("tokens_input", 0),
                tokens_output=raw_result.get("tokens_output", 0),
            )
            return parsed

        tracer.llm_done(
            parsed_preview=result_text[:800],
            duration_ms=duration_ms,
            tokens_input=raw_result.get("tokens_input", 0),
            tokens_output=raw_result.get("tokens_output", 0),
        )
        return result_text

    def _call_with_retry(self, messages: list[dict], **api_kwargs) -> dict:
        """带重试机制的 API 调用

        重试策略:
        - 网络错误 / 超时: 最多重试 max_retries 次
        - 限流 (429): 指数退避后重试
        - 鉴权失败 (401/403): 不重试，直接报错
        """
        last_error = None

        for attempt in range(self.max_retries):
            try:
                return self._call_api_once(messages, **api_kwargs)
            except Exception as e:
                last_error = e
                error_str = str(e).lower()

                # 鉴权失败 -> 不重试
                if "401" in error_str or "403" in error_str or "unauthorized" in error_str or "authentication" in error_str:
                    raise RuntimeError(f"LLM API 鉴权失败: {e}")

                # 最后一次尝试也失败了 -> 抛出
                if attempt == self.max_retries - 1:
                    raise RuntimeError(f"LLM API 调用失败 (已重试 {self.max_retries} 次) : {e}")

                # 限流 -> 指数退避
                if "429" in error_str or "rate_limit" in error_str:
                    wait = 2 ** (attempt + 1)
                else:
                    wait = 1

                logger.warning(f"LLM API 调用第 {attempt+1} 次失败，{wait}s 后重试: {e}")
                time.sleep(wait)

        raise RuntimeError(f"LLM API 调用最终失败: {last_error}")

    def _call_api_once(self, messages: list[dict], **api_kwargs) -> dict:
        """单次 API 调用 (无重试) """
        if self._client is None:
            raise RuntimeError(f"LLM 客户端未初始化 ({self.provider} SDK 可能未安装) ")

        adapter = self.config["adapter"]
        if adapter == "anthropic":
            return self._call_anthropic(messages, **api_kwargs)
        elif adapter == "openai":
            return self._call_openai(messages, **api_kwargs)
        else:
            raise RuntimeError(f"不支持的 adapter: {adapter}")

    # ──────────────────────────────────────────────
    # 各厂商具体的 API 调用
    # ──────────────────────────────────────────────

    def _call_anthropic(self, messages: list[dict], **api_kwargs) -> dict:
        """调用 Anthropic Messages API (流式)

        Returns:
            dict: {content, thinking, tokens_input, tokens_output}
        """
        system_content = None
        api_messages = []

        for msg in messages:
            if msg["role"] == "system":
                system_content = msg["content"]
            else:
                api_messages.append({"role": msg["role"], "content": msg["content"]})

        kwargs = {
            "model": self.model,
            "messages": api_messages,
            "max_tokens": api_kwargs.get("max_tokens") or 4096,
        }
        if api_kwargs.get("temperature") is not None:
            kwargs["temperature"] = api_kwargs["temperature"]
        if system_content:
            kwargs["system"] = system_content

        content_parts: list[str] = []
        thinking_parts: list[str] = []
        tokens_input = 0
        tokens_output = 0

        with self._client.messages.stream(**kwargs) as stream:
            for text in stream.text_stream:
                content_parts.append(text)
                tracer.stream_token("content", text)
            final = stream.get_final_message()
            usage = getattr(final, "usage", None)
            if usage is not None:
                tokens_input = getattr(usage, "input_tokens", 0) or 0
                tokens_output = getattr(usage, "output_tokens", 0) or 0

        return {
            "content": "".join(content_parts),
            "thinking": "".join(thinking_parts),
            "tokens_input": tokens_input,
            "tokens_output": tokens_output,
        }

    def _call_openai(self, messages: list[dict], **api_kwargs) -> dict:
        """调用 OpenAI 兼容 Chat Completion API (流式，GPT / DeepSeek / Qwen / GLM ...)

        Returns:
            dict: {content, thinking, tokens_input, tokens_output}
        """
        call_kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        # 原生思考链(reasoning_content)：底层推理由 _call_openai 流式捕获到 tracer。
        # 机械映射类任务（如 B0）对 OpenAI 兼容厂商关闭原生思考，避免拖慢/不稳定；推理任务保留。
        # 原生思考可能占用 max_tokens 导致 content 为空，由 chat() 的空输出重试兜底。
        if self.config["adapter"] == "openai" and getattr(self, "_active_prompt", "") in NO_THINKING_PROMPTS:
            call_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        # 传递 temperature / max_tokens (None 则不传，让 API 用默认值)
        if api_kwargs.get("temperature") is not None:
            call_kwargs["temperature"] = api_kwargs["temperature"]
        if api_kwargs.get("max_tokens") is not None:
            call_kwargs["max_tokens"] = api_kwargs["max_tokens"]
        else:
            call_kwargs["max_tokens"] = 4096

        response = self._client.chat.completions.create(**call_kwargs)

        content_parts: list[str] = []
        thinking_parts: list[str] = []
        usage = None
        for chunk in response:
            # 部分厂商会在末 chunk 携带 usage（流式默认不保证）
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                usage = chunk_usage
                continue
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            if delta is None:
                continue
            # 原生思考链 (DeepSeek-R1 / 智谱 thinking 系列)
            rc = getattr(delta, "reasoning_content", None)
            if rc:
                thinking_parts.append(rc)
                tracer.stream_token("thinking", rc)
            if delta.content:
                content_parts.append(delta.content)
                tracer.stream_token("content", delta.content)

        content = "".join(content_parts)

        # token 统计: 优先取 API usage，否则粗略估算
        if usage is not None:
            tokens_input = getattr(usage, "prompt_tokens", 0) or 0
            tokens_output = getattr(usage, "completion_tokens", 0) or 0
        else:
            tokens_input = self.count_tokens(_messages_to_text(messages))
            tokens_output = self.count_tokens(content)

        return {
            "content": content,
            "thinking": "".join(thinking_parts),
            "tokens_input": tokens_input,
            "tokens_output": tokens_output,
        }

    # ──────────────────────────────────────────────
    # 结构化输出解析
    # ──────────────────────────────────────────────

    def _parse_structured(self, raw: str, schema: Type[BaseModel]) -> BaseModel:
        """解析 LLM 输出为 Pydantic 校验过的结构化对象

        流程: parse_json -> validate_against_schema
        """
        data = self._response_parser.parse_json(raw)
        return self._response_parser.validate_against_schema(data, schema)

    # ──────────────────────────────────────────────
    # 公共工具方法
    # ──────────────────────────────────────────────

    def count_tokens(self, text: str) -> int:
        """估算一段文本的 token 数量

        中文约 1 token/字，英文约 1 token/4 字符
        这里使用保守估算: len(text) // 2
        """
        return len(text) // 2

    def estimate_cost(self, prompt_tokens: int, output_tokens: int) -> float:
        """估算 API 调用费用 (美元) 

        注意: 这只是粗略估算，实际价格以各厂商官网为准
        """
        prices = {
            "anthropic": {
                "claude-sonnet-5-20250610": (3.0, 15.0),
                "claude-opus-5-20250610": (15.0, 75.0),
            },
            "openai": {
                "gpt-4o-2025-06-01": (2.5, 10.0),
                "gpt-4o-mini": (0.15, 0.6),
            },
            "deepseek": {
                "deepseek-chat": (0.27, 1.10),
            },
        }

        model_prices = prices.get(self.provider, {}).get(self.model, (0, 0))
        input_price_per_m = model_prices[0]
        output_price_per_m = model_prices[1]

        cost = (prompt_tokens / 1_000_000 * input_price_per_m +
                output_tokens / 1_000_000 * output_price_per_m)
        return round(cost, 6)

    def __repr__(self) -> str:
        return f"LLMClient(provider={self.provider!r}, model={self.model!r}, adapter={self.config['adapter']!r})"
