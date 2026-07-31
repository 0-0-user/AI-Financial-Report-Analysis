"""第0层: PDF数据提取 — 使用 MinerU + LLM 表分类

流程:
1. TOCSplitter: 解析目录章节 → 在 ≤200 页处拆分 PDF
2. MineruClient: 调用 MinerU API 提取各分块
3. MineruConverter: 将 MinerU JSON 转为标准 pages 格式
4. text_corrector: OCR 纠错（复用已有）
5. DocumentChunker: 切分为四大区块（含 LLM 表分类）

输出: RawDocument (与 pdfplumber 版本完全兼容)
"""

from pathlib import Path

from pipeline.step_registry import registry
from pipeline.context import PipelineContext
from pipeline.tracer import tracer
from schemas.raw_doc import RawDocument

from .toc_splitter import TOCSplitter
from .mineru_client import MineruClient
from .mineru_converter import MineruConverter
from .chunker import DocumentChunker
from .text_corrector import correct_raw_document


@registry.register("layer_0", requires=["_pdf_path"])
def run(ctx: PipelineContext) -> None:
    """注册为 layer_0 步骤 — MinerU 集成版"""
    pdf_path = getattr(ctx, "_pdf_path", None)
    if not pdf_path:
        raise ValueError("未指定 PDF 文件路径")

    # 1. TOC 解析 → 拆分 PDF (≤200 页限制)
    #    分块保存到 data/raw/:  {原始文件名}-1.pdf, {原始文件名}-2.pdf
    splitter = TOCSplitter(pdf_path)
    chunks = splitter.split(max_pages=200)
    tracer.milestone("L0", "TOC定位+拆分", "success", f"拆为 {len(chunks)} 块")

    # 2. MinerU API 批量提取 → 合并 content_list
    #    MinerU JSON 也保存到 data/raw/
    mineru_out = Path(pdf_path).parent / "mineru_output"
    client = MineruClient()
    if len(chunks) > 1:
        content_list = client.batch_extract(chunks, output_dir=mineru_out)
    else:
        # 无需拆分，直接提取
        data = client.extract(chunks[0], output_dir=mineru_out)
        content_list = data if isinstance(data, list) else data.get("content_list", [])
    tracer.milestone("L0", "MinerU解析", "success", f"content_list {len(content_list)} 条")

    # 3. MinerU JSON → 标准 pages 格式
    converter = MineruConverter()
    raw_pages = converter.to_pages_format(
        content_list,
        file_name=pdf_path,
        page_count=splitter._get_page_count(),
    )
    tracer.milestone("L0", "转标准页格式", "success", f"{len(raw_pages.get('pages', []))} 页")

    # 4. OCR 纠错
    raw_pages = correct_raw_document(raw_pages)

    # 5. Chunker（含 LLM 表分类）
    chunker = DocumentChunker(raw_pages)
    chunked = chunker.chunk()

    # 统计四大区块规模
    fs = chunked.get("financial_data", {})
    bs = len(fs.get("balance_sheet", []))
    inc = len(fs.get("income_statement", []))
    cf = len(fs.get("cashflow_statement", []))
    md = len(chunked.get("management_discussion", {}).get("sections", []))
    tracer.milestone(
        "L0", "四大区块产出", "success",
        f"财务行 BS{bs}/PL{inc}/CF{cf}, 管理层讨论 {md} 节",
    )

    ctx.raw_doc = RawDocument(**chunked)
