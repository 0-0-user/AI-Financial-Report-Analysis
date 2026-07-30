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
"""

import logging

logger = logging.getLogger(__name__)

from pipeline.context import PipelineContext
from pipeline.step_registry import registry

# 导入各层触发 @registry.register 注册 (必须在首次使用 registry 之前) 
import layers  # noqa: F401

from schemas.report import Report, OverallAssessment, ScoreBreakdown, PeerComparison


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

    def run(self, pdf_path: str) -> Report:
        """执行完整流水线"""
        ctx = PipelineContext()
        ctx._pdf_path = pdf_path

        for step_name in self._step_order:
            # 前置依赖检查: 缺字段则直接终止
            missing = registry.check_requirements(step_name, ctx)
            if missing:
                ctx.errors.append(
                    f"步骤 '{step_name}' 前置依赖不满足，缺少字段: {', '.join(missing)}，终止流水线"
                )
                break

            try:
                step_func = registry.get(step_name)
                step_func(ctx)
            except KeyError:
                ctx.errors.append(f"步骤 '{step_name}' 未注册，终止流水线")
                break
            except Exception as e:
                ctx.errors.append(f"步骤 '{step_name}' 失败: {e}")
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
