"""第0层：PDF数据提取 — 将年报PDF解析为结构化JSON"""

from pipeline.step_registry import registry
from pipeline.context import PipelineContext
from schemas.raw_doc import RawDocument


@registry.register("layer_0")
def run(ctx: PipelineContext) -> None:
    """注册为 layer_0 步骤"""
    # pdf_path 需从外部传入，这里通过 ctx 扩展机制
    pdf_path = getattr(ctx, "_pdf_path", None)
    if not pdf_path:
        raise ValueError("未指定 PDF 文件路径")

    parser = PDFParser(pdf_path)
    raw_data = parser.extract()

    chunker = DocumentChunker(raw_data)
    chunked = chunker.chunk()

    merger = TableMerger(chunked["financial_data"])
    merged = merger.merge_cross_page_tables()
    chunked["financial_data"] = merged

    ctx.raw_doc = RawDocument(**chunked)
