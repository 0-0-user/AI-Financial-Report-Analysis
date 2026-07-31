"""
==========================================================
 pipeline/tracer.py — 全流程运行追踪器（"流水账"）
==========================================================

一个全局记账员，记录整个流水线运行过程中的两类事件：
- 里程碑节点：每一层干了什么、成了没、结果是什么
- LLM 调用：问的哪个模板、什么模型、输入了啥、思考过程、
             输出内容（流式增量）、耗时、成功失败

记账方式：
- 每条事件实时追加写进 data/logs/{文件名}_trace.jsonl（一行一条）
- 大模型"边答边记"：流式 token 每蹦一个字就记一行，日志实时增长，
  不受"单次输出上限"截断
- 跑完可从事件流重建一份 Markdown 流水账（data/logs/{文件名}_trace.md）

事件类型（统一 dict，seq 自增）：
- milestone : {type, layer, step, status, detail, duration_ms}
- llm_start : {type, prompt, layer, model, input_preview}
- llm_token : {type, prompt, kind(thinking|content), text}
- llm_done  : {type, prompt, status, duration_ms, tokens_input, tokens_output, parsed_preview}
- llm_fail  : {type, prompt, error, duration_ms}

为将来任何界面（如 VSCode 扩展）留好数据源：直接消费 jsonl 事件流即可。
"""

import json
import logging
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# 默认截断长度
_PREVIEW_LIMIT = 500


def _now_ts() -> str:
    """当前时间，格式 HH:MM:SS"""
    return time.strftime("%H:%M:%S", time.localtime())


def _clip(text: str, limit: int = _PREVIEW_LIMIT) -> str:
    """截断超长文本"""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"...(省略 {len(text) - limit} 字)"


def _split_thinking(raw: str) -> tuple[str | None, str]:
    """从 LLM 输出中分离"思考过程"与"最终结果"

    与 llm.response_parser.ResponseParser.split_thinking 保持一致，
    但放在本模块内，避免 pipeline → llm 的跨包循环导入。
    """
    if not raw:
        return None, raw
    m = re.search(
        r"(?:思考过程|思考)\s*[:：]\s*(.*?)\s*(?:最终结果|最终输出)\s*[:：]",
        raw,
        re.DOTALL,
    )
    if m:
        return m.group(1).strip(), raw[m.end():].strip()
    return None, raw


class PipelineTracer:
    """全流程记账员（进程内全局单例）"""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """清空所有事件和当前状态（新一次流水线运行前调用）"""
        self._seq = 0
        self.events: list[dict] = []
        self._log_path: Path | None = None
        self._active: dict | None = None   # 当前打开的 llm 调用
        self._active_tokens: dict | None = None  # 当前调用累计的 thinking/content

    # ──────────────────────────────────────────
    # 文件记账
    # ──────────────────────────────────────────

    def open_log(self, stem: str, log_dir: str = "data/logs") -> Path:
        """打开本次运行的 JSONL 日志文件（增量追加）

        Returns:
            日志文件完整路径
        """
        d = Path(log_dir)
        d.mkdir(parents=True, exist_ok=True)
        self._log_path = d / f"{stem}_trace.jsonl"
        return self._log_path

    def close_log(self) -> None:
        """收尾（主动关闭当前 llm 调用，防止流式中途异常）"""
        if self._active is not None:
            self.llm_fail(error="中断：日志关闭时 LLM 调用未完成")

    def _append(self, event: dict) -> None:
        """写一条事件：进内存 + 实时追加 JSONL"""
        self._seq += 1
        event["seq"] = self._seq
        event["ts"] = _now_ts()
        self.events.append(event)
        if self._log_path is not None:
            try:
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(event, ensure_ascii=False) + "\n")
            except OSError as e:
                logger.error(f"tracer 写日志失败: {e}")

    # ──────────────────────────────────────────
    # 里程碑事件
    # ──────────────────────────────────────────

    def milestone(self, layer: str, step: str, status: str, detail: str = "", duration_ms: float = 0.0) -> None:
        """记录一个里程碑节点。

        Args:
            layer: 层名，如 "L0" / "A0" / "D1路1" / "E"
            step: 里程碑名，如 "TOC拆分"
            status: "start" | "success" | "failed"
            detail: 说明（中间产物、结果、错误信息）
            duration_ms: 该阶段耗时（可选）
        """
        self._append({
            "type": "milestone",
            "layer": layer,
            "step": step,
            "status": status,
            "detail": detail,
            "duration_ms": round(duration_ms, 1),
        })

    # ──────────────────────────────────────────
    # LLM 调用事件
    # ──────────────────────────────────────────

    def llm_start(self, prompt: str, layer: str, model: str, input_preview: str = "") -> None:
        """开始一次 LLM 调用"""
        if self._active is not None:
            # 上一个调用未正常收尾（异常路径兜底）
            self.llm_fail(error="前一次调用未正常结束")
        self._active = {
            "type": "llm_start",
            "prompt": prompt,
            "layer": layer,
            "model": model,
            "input_preview": _clip(input_preview),
        }
        self._active_tokens = {"thinking": [], "content": []}
        self._append(self._active.copy())

    def stream_token(self, kind: str, text: str) -> None:
        """流式增量：大模型每蹦一个字就记一行（kind: thinking | content）"""
        if self._active is None or self._active_tokens is None:
            return
        if kind not in ("thinking", "content"):
            kind = "content"
        self._active_tokens[kind].append(text)
        self._append({
            "type": "llm_token",
            "prompt": self._active["prompt"],
            "kind": kind,
            "text": text,
        })

    def llm_done(
        self,
        parsed_preview: str = "",
        duration_ms: float = 0.0,
        tokens_input: int = 0,
        tokens_output: int = 0,
    ) -> None:
        """LLM 调用成功结束"""
        if self._active is None:
            return
        self._append({
            "type": "llm_done",
            "prompt": self._active["prompt"],
            "status": "success",
            "duration_ms": round(duration_ms, 1),
            "tokens_input": tokens_input,
            "tokens_output": tokens_output,
            "parsed_preview": _clip(parsed_preview),
        })
        self._active = None
        self._active_tokens = None

    def llm_fail(self, error: str = "", duration_ms: float = 0.0) -> None:
        """LLM 调用失败"""
        if self._active is None:
            return
        self._append({
            "type": "llm_fail",
            "prompt": self._active["prompt"],
            "error": _clip(error, limit=300),
            "duration_ms": round(duration_ms, 1),
        })
        self._active = None
        self._active_tokens = None

    # ──────────────────────────────────────────
    # 渲染 Markdown 流水账
    # ──────────────────────────────────────────

    def render_markdown(self, summary_only: bool = False) -> str:
        """从事件流重建 Markdown 流水账

        summary_only=True: 精简版——每条 LLM 调用只保留"状态 + 🧠思考过程"，
        跳过输入摘要/原始输出/解析结果（那些 JSON 是给系统处理的，不展示给人看）。
        """
        if not self.events:
            return "# 运行追踪日志\n\n(无事件记录)"

        # 组装 LLM 调用：按 llm_start 聚合后续 token 与 done/fail
        calls: list[dict] = []
        cur: dict | None = None
        for ev in self.events:
            t = ev.get("type")
            if t == "llm_start":
                cur = {
                    "seq": ev["seq"], "ts": ev["ts"], "prompt": ev.get("prompt", ""),
                    "layer": ev.get("layer", ""), "model": ev.get("model", ""),
                    "input_preview": ev.get("input_preview", ""),
                    "thinking": "", "output": "", "status": "?", "detail": "",
                }
                calls.append(cur)
            elif t == "llm_token" and cur is not None:
                if ev.get("kind") == "thinking":
                    cur["thinking"] += ev.get("text", "")
                else:
                    cur["output"] += ev.get("text", "")
            elif t == "llm_done" and cur is not None:
                cur["status"] = "✅ 成功"
                cur["detail"] = (
                    f"{ev.get('duration_ms', 0)}ms · 输入{ev.get('tokens_input', 0)}token "
                    f"· 输出{ev.get('tokens_output', 0)}token"
                )
                cur["parsed_preview"] = ev.get("parsed_preview", "")
            elif t == "llm_fail" and cur is not None:
                cur["status"] = "❌ 失败"
                cur["detail"] = f"{ev.get('duration_ms', 0)}ms · {ev.get('error', '')}"
                cur["parsed_preview"] = ""

        # 统计每层概览
        layers: dict[str, dict] = {}
        for ev in self.events:
            if ev.get("type") != "milestone":
                continue
            layer = ev.get("layer", "")
            entry = layers.setdefault(layer, {"status": "", "duration_ms": 0.0, "llm_calls": 0, "failures": 0})
            if ev.get("status") == "start":
                continue
            entry["status"] = "✅" if ev.get("status") == "success" else "❌"
            entry["duration_ms"] += ev.get("duration_ms", 0.0)
        for ev in self.events:
            if ev.get("type") == "llm_start":
                layer = ev.get("layer", "")
                layers.setdefault(layer, {"status": "", "duration_ms": 0.0, "llm_calls": 0, "failures": 0})
                layers[layer]["llm_calls"] += 1
            elif ev.get("type") == "llm_fail":
                layer = ev.get("layer", "")
                layers.setdefault(layer, {"status": "", "duration_ms": 0.0, "llm_calls": 0, "failures": 0})
                layers[layer]["failures"] += 1

        lines: list[str] = []
        lines.append("# 运行追踪日志")
        lines.append("")

        lines.append("## 一、概览")
        lines.append("")
        lines.append("| 层 | 状态 | 累计耗时 | LLM 调用数 | 失败 |")
        lines.append("|----|------|----------|-----------|------|")
        for layer, st in layers.items():
            lines.append(
                f"| {layer} | {st['status']} | {st['duration_ms'] / 1000:.1f}s | "
                f"{st['llm_calls']} | {st['failures']} |"
            )
        lines.append("")

        lines.append("## 二、LLM 思考链明细")
        lines.append("")
        if not calls:
            lines.append("(本次运行没有 LLM 调用)")
        for i, c in enumerate(calls, 1):
            # 流式原始输出可能含"思考过程:...最终结果:..."，再剥离一次展示更清晰
            exp_thinking, exp_result = _split_thinking(c["output"])
            thinking_show = (c["thinking"] or exp_thinking or "").strip()
            output_show = exp_result if exp_thinking else c["output"]

            lines.append(f"### 调用 #{i} — {c['layer']} {c['prompt']} · {c['model']}")
            lines.append("")
            lines.append(f"- **状态**: {c['status']}  ({c['detail']})")
            lines.append("")
            if thinking_show:
                lines.append("🧠 **思考过程**:")
                lines.append("")
                lines.append("```")
                lines.append(_clip(thinking_show, limit=2000))
                lines.append("```")
                lines.append("")
            if summary_only:
                continue  # 精简版：不展示输入摘要/原始输出/解析结果（JSON 给系统处理）
            if c["input_preview"]:
                lines.append("📤 **输入摘要**:")
                lines.append("")
                lines.append("```")
                lines.append(_clip(c["input_preview"], limit=2000))
                lines.append("```")
                lines.append("")
            if output_show:
                lines.append("📥 **原始输出**:")
                lines.append("")
                lines.append("```")
                lines.append(_clip(output_show, limit=2000))
                lines.append("```")
                lines.append("")
            if c.get("parsed_preview"):
                lines.append("✅ **解析结果**:")
                lines.append("")
                lines.append("```")
                lines.append(c["parsed_preview"])
                lines.append("```")
                lines.append("")

        lines.append("## 三、里程碑节点")
        lines.append("")
        # 按层分组
        by_layer: dict[str, list[dict]] = {}
        for ev in self.events:
            if ev.get("type") == "milestone":
                by_layer.setdefault(ev.get("layer", ""), []).append(ev)
        if not by_layer:
            lines.append("(无里程碑)")
        for layer, evs in by_layer.items():
            lines.append(f"### {layer}")
            lines.append("")
            for ev in evs:
                icon = {"start": "▶️", "success": "✅", "failed": "❌"}.get(ev.get("status"), "•")
                detail = ev.get("detail", "")
                lines.append(f"- [{ev.get('ts', '')}] {icon} **{ev.get('step', '')}** {detail}".rstrip())
            lines.append("")

        return "\n".join(lines)

    def save_markdown(self, path: str | Path) -> None:
        """保存 Markdown 流水账到文件"""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.render_markdown(), encoding="utf-8")
        logger.info(f"运行流水账已保存: {p}")


# 全局单例
tracer = PipelineTracer()
