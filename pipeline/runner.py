"""pipeline/runner.py — 可复用的单份年报分析入口（供 Web 界面调用）

等价于 scripts/run_pipeline.py 的命令行流程，但封装为函数，
可被 Web 后端（web/app.py）在后台线程调用，并保留流水账/报告产出。
"""

import json
import logging
import threading
from pathlib import Path

from schemas.report import Report

logger = logging.getLogger(__name__)


class AnalysisControl:
    """分析任务控制：取消（真正终止）与暂停（可继续接上）

    - cancel: 置位后管线在层间检查点抛出终止，任务标记 cancelled，不再后台跑完
    - pause:  置位后管线在层间检查点等待；clear 后从下一层继续
    """

    def __init__(self) -> None:
        self.cancel = threading.Event()
        self.pause = threading.Event()


def run_single_analysis(pdf_path: str, controls: AnalysisControl | None = None) -> Report:
    """对一份年报执行完整分析（0层→E层），保存 trace 与报告

    Args:
        pdf_path: 年报 PDF 路径
        controls: 任务控制（取消/暂停），None 表示不启用

    Returns:
        最终 Report（含总分/异常/报告文本）
    """
    from pipeline.orchestrator import Orchestrator
    from pipeline.tracer import tracer

    pdf = Path(pdf_path)
    if not pdf.exists():
        raise FileNotFoundError(f"PDF 不存在: {pdf_path}")
    stem = pdf.stem

    # 打开流水账（实时写 jsonl，供前端 SSE 推送）
    tracer.reset()
    tracer.open_log(stem, "data/logs")

    try:
        orch = Orchestrator()
        report = orch.run(str(pdf), controls=controls)
    except Exception as e:
        if controls is not None and controls.cancel.is_set():
            raise RuntimeError("分析已取消") from e
        raise
    finally:
        tracer.close_log()

    # 保存流水账 Markdown
    logs_dir = Path("data/logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    tracer.save_markdown(logs_dir / f"{stem}_trace.md")

    # 保存报告 JSON + Markdown
    output_dir = Path("data/outputs")
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{stem}_report.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report.model_dump(), f, ensure_ascii=False, indent=2)

    try:
        from layers.layer_e_output.e2_report_gen import render_to_markdown
        md_content = render_to_markdown(report)
        with open(output_dir / f"{stem}_report.md", "w", encoding="utf-8") as f:
            f.write(md_content)
    except Exception as e:
        logger.warning(f"Markdown 渲染失败（非阻断）: {e}")

    return report
