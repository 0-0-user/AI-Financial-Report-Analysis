"""
==========================================================
 scripts/run_pipeline.py — 命令行入口: 运行完整分析流水线
==========================================================

用户的唯一入口脚本。通过命令行参数控制流水线行为。

核心功能:
1. 指定要分析的 PDF 文件路径 (必填)
2. 指定报告输出目录 (可选，默认 data/outputs/)
3. 从指定步骤开始运行 (可选，调试用)
4. 全程记录"流水账"日志: 里程碑 + 大模型思考链
   - data/logs/{文件名}_trace.md   (Markdown 流水账)
   - data/logs/{文件名}_trace.jsonl (事件流原稿，实时增长)
   - data/logs/{文件名}.log         (运行日志)

使用方式:
    # 基本用法
    python scripts/run_pipeline.py --pdf data/raw/600519_2024.pdf

    # 从 C 层开始 (跳过之前步骤)
    python scripts/run_pipeline.py --pdf xxx.pdf --from layer_c

    # 自定义输出目录
    python scripts/run_pipeline.py --pdf xxx.pdf --output data/outputs
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# Windows GBK 终端兼容
if sys.stdout.encoding and sys.stdout.encoding.lower() in ("gbk", "gb2312", "gb18030"):
    import contextlib
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from pipeline.orchestrator import Orchestrator
from pipeline.tracer import tracer


def _setup_logging(log_path: Path) -> None:
    """配置日志: 控制台 + 文件双输出"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handlers = [logging.StreamHandler()]
    try:
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=handlers,
    )


def main():
    parser = argparse.ArgumentParser(description="AI 财报分析流水线")
    parser.add_argument("--pdf", required=True, help="年报 PDF 文件路径")
    parser.add_argument("--output", default="data/outputs", help="报告输出目录")
    parser.add_argument("--from", dest="start_from", default=None, help="从指定步骤开始")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"❌ PDF 文件不存在: {pdf_path}")
        return

    # 日志目录 + 命名
    logs_dir = Path("data/logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    stem = pdf_path.stem

    _setup_logging(logs_dir / f"{stem}.log")
    logger = logging.getLogger("run_pipeline")
    logger.info(f"🚀 开始分析: {pdf_path.name}")

    # 打开流水账（先 reset 再 open，避免残留上一次状态）
    tracer.reset()
    jsonl_path = tracer.open_log(stem, str(logs_dir))

    orch = Orchestrator()
    try:
        report = orch.run(str(pdf_path), start_from=args.start_from)
    finally:
        tracer.close_log()

    # 收尾: 生成 Markdown 流水账
    trace_md = logs_dir / f"{stem}_trace.md"
    tracer.save_markdown(trace_md)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_stem = pdf_path.stem

    # 保存 JSON
    json_path = output_dir / f"{output_stem}_report.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report.model_dump(), f, ensure_ascii=False, indent=2)
    print(f"✅ JSON 报告: {json_path}")

    # 保存 Markdown
    try:
        from layers.layer_e_output.e2_report_gen import render_to_markdown
        md_content = render_to_markdown(report)
        md_path = output_dir / f"{output_stem}_report.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        print(f"✅ Markdown 报告: {md_path}")
    except Exception as e:
        print(f"⚠️ Markdown 渲染失败 (非阻断): {e}")

    print(f"📓 运行流水账: {trace_md}")
    print(f"📊 事件流原稿: {jsonl_path}")
    print(f"🎯 分析完成")


if __name__ == "__main__":
    main()
