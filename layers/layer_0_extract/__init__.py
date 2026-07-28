"""第0层：PDF数据提取 — 将年报PDF解析为结构化JSON"""

from pipeline.step_registry import registry
from pipeline.context import PipelineContext
from schemas.raw_doc import RawDocument
from .pdf_parser import PDFParser
from .chunker import DocumentChunker
from .merger import TableMerger
from .text_corrector import correct_raw_document


@registry.register("layer_0")
def run(ctx: PipelineContext) -> None:
    """注册为 layer_0 步骤"""
    pdf_path = getattr(ctx, "_pdf_path", None)
    if not pdf_path:
        raise ValueError("未指定 PDF 文件路径")

    parser = PDFParser(pdf_path)
    raw_data = parser.extract()

    # OCR 纠错（v2: 提高扫描件准确率）
    raw_data = correct_raw_document(raw_data)

    chunker = DocumentChunker(raw_data)
    chunked = chunker.chunk()

    merger = TableMerger(chunked["financial_data"])
    merged = merger.merge_cross_page_tables()
    chunked["financial_data"] = merged

    ctx.raw_doc = RawDocument(**chunked)
