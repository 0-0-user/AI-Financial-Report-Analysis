"""测试 LLM 网关自动埋点 + orchestrator start_from 修复"""

import json

from pipeline.orchestrator import Orchestrator
from pipeline.tracer import tracer
from llm.client import LLMClient


def test_chat_strips_thinking_and_records(monkeypatch):
    """chat() 剥离思考过程 + 自动记账到 tracer"""
    def fake_call(self, messages, **kw):
        return {
            "content": (
                "思考过程：先分析行业背景，判断搜索词要避开归因词。\n\n"
                "最终结果：\n"
                '{"维度1_宏观周期": ["中药 集采 今年"]}'
            ),
            "thinking": "",
            "tokens_input": 100,
            "tokens_output": 20,
        }
    monkeypatch.setattr("llm.client.LLMClient._call_openai", fake_call)
    tracer.reset()

    client = LLMClient(provider="glm", model="glm-4.7-flash")
    result = client.chat("a0_search_query", {"business_desc": "中药", "industry_tags": "中药", "year": "2025"})

    # 返回剥离思考链后的纯 JSON
    data = json.loads(result)
    assert data["维度1_宏观周期"] == ["中药 集采 今年"]

    # tracer 记录 start/done，层归属和模型正确
    starts = [e for e in tracer.events if e["type"] == "llm_start"]
    dones = [e for e in tracer.events if e["type"] == "llm_done"]
    assert len(starts) == 1 and len(dones) == 1
    assert starts[0]["layer"] == "A0"
    assert starts[0]["model"] == "glm-4.7-flash"


def test_chat_records_failure(monkeypatch):
    """chat() 失败时记录 llm_fail 并照常抛异常"""
    def fail_call(self, messages, **kw):
        raise RuntimeError("API 鉴权失败: 401")
    monkeypatch.setattr("llm.client.LLMClient._call_openai", fail_call)
    tracer.reset()

    client = LLMClient(provider="glm", model="glm-4.7-flash")
    try:
        client.chat("a0_search_query", {"business_desc": "x", "industry_tags": "y", "year": "2025"})
        assert False, "应抛出异常"
    except RuntimeError:
        pass

    fails = [e for e in tracer.events if e["type"] == "llm_fail"]
    assert len(fails) == 1
    assert "401" in fails[0]["error"]


def test_orchestrator_start_from_no_attribute_error():
    """start_from 参数可用，不再抛 AttributeError（旧 skip bug）"""
    tracer.reset()
    orch = Orchestrator()
    # 从 layer_c 开始，前置依赖缺失 → 返回错误报告而非崩溃
    report = orch.run("data/raw/nonexistent.pdf", start_from="layer_c")
    assert report is not None
    # tracer 记录了启动里程碑
    assert any(e["type"] == "milestone" for e in tracer.events)


def test_render_markdown_from_full_run(monkeypatch):
    """一次完整 LLM 链路后，Markdown 流水账包含里程碑与思考链"""
    def fake_call(self, messages, **kw):
        # 模拟真实流式：逐字写入 tracer
        for tok in ["思考过程：", "判断归属。", "\n\n最终结果：\n", '{"ok": true}']:
            tracer.stream_token("content", tok)
        return {
            "content": "思考过程：判断归属。\n\n最终结果：\n{\"ok\": true}",
            "thinking": "",
            "tokens_input": 10,
            "tokens_output": 5,
        }
    monkeypatch.setattr("llm.client.LLMClient._call_openai", fake_call)
    tracer.reset()

    client = LLMClient(provider="glm", model="glm-4.7-flash")
    client.chat("b0_table_localization", {"tables": [{"id": "t0", "page_idx": 1, "header_text": "x"}]})
    client.chat("d3_semantic_match", {"cause": "行业下行", "probability": 0.3, "semantic_levels": []})

    md = tracer.render_markdown()
    assert "思考过程" in md
    assert "b0_table_localization" in md
    assert "d3_semantic_match" in md
    assert "判断归属" in md
    assert "L0" in md   # b0_table_localization 归属 L0
    assert "D3" in md   # d3_semantic_match 归属 D3
