"""
==========================================================
 llm/prompt_loader.py — Prompt 模板加载器
==========================================================

从 config/prompts/ 目录下加载 YAML 格式的 prompt 模板，
并使用 Jinja2 引擎渲染变量。

设计原则：代码与 prompt 分离
- Prompt 文件放在 config/prompts/ 中，不混在代码里
- 修改 prompt 不需要改代码，非开发同学也能审核
- 模板支持 Jinja2 语法（循环、条件判断、变量插值）

使用方式：
    loader = PromptLoader()
    prompt = loader.load("a0_macro_search")
    rendered = loader.render("a0_macro_search", {"query": "光伏行业"})
"""

from pathlib import Path
import yaml
from jinja2 import Template


class PromptLoader:
    """从 config/prompts/ 加载 YAML prompt 模板"""

    def __init__(self, prompts_dir: str = "config/prompts"):
        self.prompts_dir = Path(prompts_dir)
        self._cache: dict[str, dict] = {}

    def load(self, name: str) -> dict:
        """按 name 加载 prompt，支持缓存"""
        if name in self._cache:
            return self._cache[name]

        file_path = self.prompts_dir / f"{name}.yaml"
        if not file_path.exists():
            raise FileNotFoundError(f"Prompt 模板未找到: {name}")

        with open(file_path, encoding="utf-8") as f:
            prompt_data = yaml.safe_load(f)

        self._cache[name] = prompt_data
        return prompt_data

    def render(self, name: str, variables: dict) -> list[dict]:
        """加载并渲染 prompt 模板，返回适合 API 调用的消息列表

        每一条消息格式: {"role": "system"/"user"/"assistant", "content": "..."}
        """
        prompt_data = self.load(name)
        messages = prompt_data.get("messages", [])

        rendered = []
        for msg in messages:
            template = Template(msg["content"])
            rendered_content = template.render(**variables)
            rendered.append({"role": msg["role"], "content": rendered_content})

        return rendered
