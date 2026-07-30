"""
==========================================================
 scripts/run_pipeline.py — 命令行入口: 运行完整分析流水线
==========================================================

用户的唯一入口脚本。通过命令行参数控制流水线行为。

核心功能: 
1. 指定要分析的 PDF 文件路径 (必填) 
2. 指定报告输出目录 (可选，默认 data/outputs/) 
3. 从指定步骤开始运行 (可选，调试用) 
4. 跳过 LLM 调用 (可选，复用缓存时用) 

使用方式: 
    # 基本用法
    python scripts/run_pipeline.py --pdf data/raw/600519_2024.pdf

    # 从 C 层开始 (跳过之前步骤) 
    python scripts/run_pipeline.py --pdf xxx.pdf --from layer_c

    # 不调 LLM (用缓存数据) 
    python scripts/run_pipeline.py --pdf xxx.pdf --no-llm
"""

import argparse
import json
import sys
from pathlib import Path

# Windows GBK 终端兼容
if sys.stdout.encoding and sys.stdout.encoding.lower() in ("gbk", "gb2312", "gb18030"):
    import contextlib
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from pipeline.orchestrator import Orchestrator


def main():
    parser = argparse.ArgumentParser(description="AI 财报分析流水线")
    parser.add_argument("--pdf", required=True, help="年报 PDF 文件路径")
    parser.add_argument("--output", default="data/outputs", help="报告输出目录")
    parser.add_argument("--from", dest="start_from", default=None, help="从指定步骤开始")
    parser.add_argument("--no-llm", action="store_true", help="跳过所有 LLM 调用")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"❌ PDF 文件不存在: {pdf_path}")
        return

    orch = Orchestrator()
    if args.start_from:
        # 跳过起始步骤之前的所有步骤
        step_order = ["layer_0", "layer_a", "layer_b", "layer_amacro", "layer_bplus", "layer_c", "layer_d", "layer_e"]
        for step in step_order:
            if step == args.start_from:
                break
            orch.skip(step)

    print(f"🚀 开始分析: {pdf_path.name}")
    report = orch.run(str(pdf_path))

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

    print(f"🎯 分析完成")


if __name__ == "__main__":
    main()
