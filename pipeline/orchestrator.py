"""主调度器——按顺序执行 0→A→B→B+→C→D→E"""

from pipeline.context import PipelineContext
from pipeline.step_registry import registry

from schemas.report import Report


class Orchestrator:
    """流水线引擎，调度各层执行"""

    def __init__(self):
        self._step_order = [
            "layer_0",
            "layer_a",
            "layer_b",
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

        for step_name in self._step_order:
            if step_name in self._skip_steps:
                continue

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
            overall_assessment={"status": "失败", "errors": ctx.errors},
            core_anomalies=[],
            bull_points=[],
            bear_points=[],
        )
