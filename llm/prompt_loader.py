"""加载和渲染 prompt 模板"""

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

    def render(self, name: str, variables: dict) -> str:
        """加载并渲染 prompt 模板"""
        prompt_data = self.load(name)
        messages = prompt_data.get("messages", [])

        rendered = []
        for msg in messages:
            template = Template(msg["content"])
            rendered_content = template.render(**variables)
            rendered.append({"role": msg["role"], "content": rendered_content})

        return str(rendered)
