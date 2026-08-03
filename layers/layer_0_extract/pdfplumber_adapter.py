"""pdfplumber 降级适配器 — 当 MinerU 不可用时回退

输出格式与 MineruConverter.to_pages_format() 完全兼容:
  {"metadata": {...}, "pages": [{"page_num": int, "text": str, "tables": [...]}]}
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def extract_with_pdfplumber(pdf_path: str) -> dict:
    """使用 pdfplumber 提取 PDF 文本和表格

    Returns:
        {"metadata": {...}, "pages": [{"page_num": int, "text": str, "tables": [...]}]}
    """
    try:
        import pdfplumber
    except ImportError:
        raise ImportError("pdfplumber 未安装: pip install pdfplumber")

    file_name = Path(pdf_path).name

    metadata = {
        "file_name": file_name,
        "page_count": 0,
        "report_year": _guess_year(file_name),
        "extract_tool": "pdfplumber",
        "extract_date": datetime.now().isoformat(),
    }

    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        metadata["page_count"] = len(pdf.pages)

        for page_num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            raw_tables = page.extract_tables()

            tables = []
            for ti, table in enumerate(raw_tables):
                if table and len(table) > 1:  # 至少表头+数据
                    tables.append({
                        "table_index": ti,
                        "page_number": page_num,
                        "rows": table,
                        "row_count": len(table),
                        "col_count": len(table[0]) if table[0] else 0,
                    })

            pages.append({
                "page_num": page_num,
                "text": text,
                "tables": tables,
            })

    logger.info(
        f"pdfplumber 解析: {metadata['page_count']} 页, "
        f"{sum(len(p['tables']) for p in pages)} 张表"
    )
    return {"metadata": metadata, "pages": pages}


def _guess_year(file_name: str) -> Optional[int]:
    years = re.findall(r"(20\d{2})", file_name)
    return int(years[-1]) if years else None
