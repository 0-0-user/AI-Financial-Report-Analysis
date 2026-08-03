"""第0层: PDF数据提取 — MinerU 优先 / pdfplumber 降级 + LLM 表分类"""

from pathlib import Path

from pipeline.step_registry import registry
from pipeline.context import PipelineContext
from pipeline.tracer import tracer
from schemas.raw_doc import RawDocument

from .chunker import DocumentChunker
from .text_corrector import correct_raw_document


def _extract_with_mineru(pdf_path: str) -> dict:
    """MinerU 提取（需要安装 CLI）"""
    from .toc_splitter import TOCSplitter
    from .mineru_client import MineruClient
    from .mineru_converter import MineruConverter

    splitter = TOCSplitter(pdf_path)
    chunks = splitter.split(max_pages=200)
    tracer.milestone("L0", "TOC拆分", "success", f"{len(chunks)} 块")

    mineru_out = Path(pdf_path).parent / "mineru_output"
    client = MineruClient()
    if len(chunks) > 1:
        content_list = client.batch_extract(chunks, output_dir=mineru_out)
    else:
        data = client.extract(chunks[0], output_dir=mineru_out)
        content_list = data if isinstance(data, list) else data.get("content_list", [])
    tracer.milestone("L0", "MinerU解析", "success", f"{len(content_list)} 条")

    converter = MineruConverter()
    return converter.to_pages_format(content_list, file_name=pdf_path,
                                     page_count=splitter._get_page_count())


@registry.register("layer_0", requires=["_pdf_path"])
def run(ctx: PipelineContext) -> None:
    pdf_path = getattr(ctx, "_pdf_path", None)
    if not pdf_path:
        raise ValueError("未指定 PDF 文件路径")

    # MinerU 优先, 不可用时 pdfplumber 降级
    try:
        raw_pages = _extract_with_mineru(pdf_path)
    except Exception as e:
        tracer.milestone("L0", "MinerU不可用", "warning", f"降级 pdfplumber: {e}")
        from .pdfplumber_adapter import extract_with_pdfplumber
        raw_pages = extract_with_pdfplumber(pdf_path)

    tracer.milestone("L0", "转标准页格式", "success",
                     f"{len(raw_pages.get('pages', []))} 页")

    raw_pages = correct_raw_document(raw_pages)
    chunker = DocumentChunker(raw_pages)
    chunked = chunker.chunk()

    fs = chunked.get("financial_data", {})
    bs = len(fs.get("balance_sheet", []))
    pl = len(fs.get("income_statement", []))
    cf = len(fs.get("cashflow_statement", []))
    md = len(chunked.get("management_discussion", {}).get("sections", []))
    tracer.milestone("L0", "四大区块", "success", f"BS{bs}/PL{pl}/CF{cf}, MD{md}节")

    ctx.raw_doc = RawDocument(**chunked)
