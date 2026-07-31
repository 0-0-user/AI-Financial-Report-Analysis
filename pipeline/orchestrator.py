"""
==========================================================
 pipeline/orchestrator.py — 主调度器
==========================================================

按标准顺序调度各层执行: 0 -> A -> B -> B+ -> C -> D -> E。
每一层的输出自动存入 PipelineContext，供下一层读取。

如果某一步失败或抛出异常，流水线会立即终止并返回错误报告。
不允许跳过任何步骤——严格执行，失败即终止。

使用方式:
    orch = Orchestrator()
    report = orch.run("年报.pdf")
    report = orch.run("年报.pdf", start_from="layer_c")  # 从 C 层开始
"""

import logging
import time

logger = logging.getLogger(__name__)

from pipeline.context import PipelineContext
from pipeline.step_registry import registry
from pipeline.tracer import tracer

# 导入各层触发 @registry.register 注册 (必须在首次使用 registry 之前)
import layers  # noqa: F401

from schemas.report import Report, OverallAssessment, ScoreBreakdown, PeerComparison

# 注册步骤名 → 日志展示层名
_LAYER_LABELS = {
    "layer_0": "L0",
    "layer_a": "A1/A2",
    "layer_b": "B0/B1",
    "layer_amacro": "A0",
    "layer_bplus": "B+",
    "layer_c": "C",
    "layer_d": "D1/D2/D3",
    "layer_e": "E1/E2",
}


class Orchestrator:
    """流水线引擎，调度各层执行 (不允许跳过，失败即终止) """

    def __init__(self):
        self._step_order = [
            "layer_0",
            "layer_a",
            "layer_b",
            "layer_amacro",
            "layer_bplus",
            "layer_c",
            "layer_d",
            "layer_e",
        ]

    def run(self, pdf_path: str, start_from: str | None = None, controls=None) -> Report:
        """执行完整流水线

        Args:
            pdf_path: 年报 PDF 路径
            start_from: 从指定步骤开始（跳过之前的步骤），如 "layer_c"
            controls: 任务控制（取消/暂停）。层间检查：取消即终止；暂停则等待可继续。
        """
        ctx = PipelineContext()
        ctx._pdf_path = pdf_path

        # 注: reset() 由调用方（scripts/run_pipeline.py）在 open_log() 前负责，
        # 否则会清掉已打开的日志文件句柄。
        tracer.milestone("Pipeline", "启动", "start", f"分析: {pdf_path}")

        # 确定起始层（支持从某层开始，替代已删除的 skip()）
        step_order = self._step_order
        if start_from:
            if start_from not in self._step_order:
                raise ValueError(f"未知步骤: {start_from}，可选: {self._step_order}")
            step_order = self._step_order[self._step_order.index(start_from):]

        for step_name in step_order:
            # 取消/暂停检查点（层间）
            if controls is not None:
                if controls.cancel.is_set():
                    raise RuntimeError("分析已取消")
                while controls.pause.is_set():
                    if controls.cancel.is_set():
                        raise RuntimeError("分析已取消")
                    time.sleep(0.2)
            layer_label = _LAYER_LABELS.get(step_name, step_name)

            # 前置依赖检查: 缺字段则直接终止
            missing = registry.check_requirements(step_name, ctx)
            if missing:
                msg = (
                    f"步骤 '{step_name}' 前置依赖不满足，缺少字段: "
                    f"{', '.join(missing)}，终止流水线"
                )
                ctx.errors.append(msg)
                tracer.milestone(layer_label, "前置检查", "failed", msg)
                break

            tracer.milestone(layer_label, "开始", "start")
            start = time.time()
            try:
                step_func = registry.get(step_name)
                step_func(ctx)
                duration_ms = (time.time() - start) * 1000
                tracer.milestone(
                    layer_label, "完成", "success",
                    f"耗时 {duration_ms / 1000:.1f}s", duration_ms,
                )
            except KeyError:
                msg = f"步骤 '{step_name}' 未注册，终止流水线"
                ctx.errors.append(msg)
                tracer.milestone(layer_label, "失败", "failed", msg)
                break
            except Exception as e:
                msg = f"步骤 '{step_name}' 失败: {e}"
                ctx.errors.append(msg)
                tracer.milestone(layer_label, "失败", "failed", str(e))
                break

        if ctx.errors:
            for err in ctx.errors:
                logger.error(err)
            return self._build_error_report(ctx)

        return ctx.report

    @staticmethod
    def _build_error_report(ctx: PipelineContext) -> Report:
        return Report(
            company_name="",
            stock_code="",
            report_year=0,
            overall_assessment=OverallAssessment(
                score=0,
                confidence_tier="低置信度",
                peer_comparisons=[],
            ),
            core_anomalies=[],
            bull_points=[],
            bear_points=[],
        )
