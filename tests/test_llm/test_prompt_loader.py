"""
==========================================================
 tests/test_llm/test_prompt_loader.py — Prompt 加载器测试
==========================================================

测试 PromptLoader 的核心功能：
- 能正确加载存在的 prompt 文件
- 不存在的文件应报 FileNotFoundError
- 模板渲染应正确填充变量
"""

import pytest
from llm.prompt_loader import PromptLoader


class TestPromptLoader:
    """Prompt 加载与渲染测试"""

    def test_load_existing_prompt(self):
        """应能加载存在的 prompt 文件"""
        loader = PromptLoader("config/prompts")
        prompt = loader.load("a0_macro_search")
        assert prompt["name"] == "a0_macro_search"

    def test_load_nonexistent_prompt(self):
        """不存在的 prompt 应报错"""
        loader = PromptLoader("config/prompts")
        with pytest.raises(FileNotFoundError):
            loader.load("nonexistent_prompt")

    def test_render_with_variables(self):
        """模板渲染应正确填充变量"""
        loader = PromptLoader("config/prompts")
        messages = loader.render("a0_macro_search", {
            "search_query": "test",
            "search_results": "result_data",
        })
        # render() 返回 list[dict]，每条含 role 和 content
        assert len(messages) > 0
        all_text = " ".join(m["content"] for m in messages)
        assert "test" in all_text
        assert "result_data" in all_text
