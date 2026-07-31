"""测试 tracer 事件流账本 + split_thinking 思考链剥离"""

import json

from pipeline.tracer import PipelineTracer
from llm.response_parser import ResponseParser


# ──────────────────────────────────────────
# split_thinking
# ──────────────────────────────────────────

def test_split_thinking_plain_json():
    """纯 JSON 无思考过程 → 原样返回"""
    raw = '{"a": 1}'
    thinking, result = ResponseParser.split_thinking(raw)
    assert thinking is None
    assert result == raw


def test_split_thinking_with_thinking():
    """标准格式：思考过程:...最终结果:{...}"""
    raw = "思考过程：先看表头，有资产和负债。\n\n最终结果：\n{\"a\": 1}"
    thinking, result = ResponseParser.split_thinking(raw)
    assert "先看表头" in thinking
    assert json.loads(result) == {"a": 1}


def test_split_thinking_variants():
    """变体：全角冒号 / "思考"开头 / "最终输出"结尾"""
    raw = "思考：有资产和负债。最终输出：{\"x\": 2}"
    thinking, result = ResponseParser.split_thinking(raw)
    assert thinking is not None
    assert json.loads(result) == {"x": 2}


def test_split_thinking_empty():
    thinking, result = ResponseParser.split_thinking("")
    assert thinking is None
    assert result == ""


# ──────────────────────────────────────────
# PipelineTracer 事件记录
# ──────────────────────────────────────────

def test_tracer_records_to_jsonl(tmp_path):
    """里程碑 + LLM 调用事件实时写入 JSONL"""
    t = PipelineTracer()
    t.open_log("test", str(tmp_path))
    t.milestone("L0", "拆分", "success", "2块")
    t.llm_start("a0_search_query", "A0", "glm-4.7-flash", "输入摘要")
    t.stream_token("content", "一")
    t.stream_token("content", "二")
    t.llm_done(parsed_preview='{"ok": true}', duration_ms=10)
    t.close_log()

    lines = (tmp_path / "test_trace.jsonl").read_text(encoding="utf-8").strip().splitlines()
    types = [json.loads(l)["type"] for l in lines]
    assert "milestone" in types
    assert "llm_start" in types
    assert "llm_token" in types
    assert "llm_done" in types
    # seq 递增
    seqs = [json.loads(l)["seq"] for l in lines]
    assert seqs == sorted(seqs)


def test_render_markdown_contains_thinking(tmp_path):
    """Markdown 流水账包含思考过程和输出"""
    t = PipelineTracer()
    t.milestone("L0", "拆分", "success", "2块")
    t.llm_start("a0_search_query", "A0", "glm-4.7-flash", "输入摘要")
    t.stream_token("thinking", "先想一下行业背景")
    t.stream_token("content", "{\"维度1\": []}")
    t.llm_done(parsed_preview='{"维度1": []}', duration_ms=10)
    t.close_log()

    md = t.render_markdown()
    assert "思考过程" in md
    assert "先想一下行业背景" in md
    assert "{\"维度1\": []}" in md
    assert "L0" in md
    assert "拆分" in md


def test_llm_fail_records_error(tmp_path):
    t = PipelineTracer()
    t.open_log("test", str(tmp_path))
    t.llm_start("d3_semantic_match", "D3", "glm-4.7-flash", "原因")
    t.llm_fail(error="API 401", duration_ms=50)
    t.close_log()

    lines = (tmp_path / "test_trace.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert any(json.loads(l)["type"] == "llm_fail" for l in lines)
