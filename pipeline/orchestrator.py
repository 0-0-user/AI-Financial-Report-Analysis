"""
==========================================================
 pipeline/orchestrator.py — 主调度器
==========================================================

按标准顺序调度各层执行：0 → A → B → B+ → C → D → E。
每一层的输出自动存入 PipelineContext，供下一层读取。

如果某一步失败或抛出异常，流水线会立即终止并返回错误报告。
支持跳过指定步骤（调试时很有用）。

使用方式：
    orch = Orchestrator()
    orch.skip("layer_c", "layer_d")  # 调试时跳过某些步骤
    report = orch.run("年报.pdf")
"""

from pipeline.context import PipelineContext
from pipeline.step_registry import registry

from schemas.report import Report, OverallAssessment, ScoreBreakdown, PeerComparison


class Orchestrator:
    """流水线引擎，调度各层执行"""

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
        self._skip_steps: set[str] = set()

    def skip(self, *step_names: str) -> "Orchestrator":
        """跳过指定步骤（调试用）"""
        for name in step_names:
            self._skip_steps.add(name)
        return self

    def run(self, pdf_path: str) -> Report:
        """执行完整流水线"""
        ctx = PipelineContext()
        ctx._pdf_path = pdf_path

        for step_name in self._step_order:
            if step_name in self._skip_steps:
                continue

            # 前置依赖检查：缺字段则直接终止
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
                ctx.warnings.append(f"步骤 '{step_name}' 未注册，跳过")
            except Exception as e:
                ctx.errors.append(f"步骤 '{step_name}' 失败: {e}")
                break

        if ctx.errors:
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
