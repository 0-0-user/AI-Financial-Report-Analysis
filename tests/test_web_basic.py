"""测试 Web 后端基础功能（轻量，不启服务、不调 LLM）"""


def test_provider_config_has_8_providers():
    """厂商配置覆盖 8 家"""
    from llm.client import PROVIDER_CONFIG
    assert len(PROVIDER_CONFIG) >= 8


def test_pipeline_runner_importable():
    """分析入口可导入"""
    from pipeline.runner import run_single_analysis
    assert callable(run_single_analysis)


def test_test_connection_prompt_renderable():
    """连通性测试 prompt 可渲染"""
    from llm.prompt_loader import PromptLoader
    loader = PromptLoader()
    messages = loader.render("test_connection", {})
    assert messages and messages[0]["role"] == "user"
    assert "OK" in messages[0]["content"]


def test_web_app_importable():
    """FastAPI 应用可导入（不启动）"""
    from web.app import app
    assert app.title == "AI 财报分析"
