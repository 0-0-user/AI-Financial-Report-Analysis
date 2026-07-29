"""第0层：PDF解析器 — 将年报PDF提取为原始文本和表格数据

唯一后端：pdfplumber（纯 Python，零额外依赖）

设计约束：
- 不涉及任何业务逻辑，只做文件→结构化数据
- 输出原始 dict，由 chunker.py 切分为 RawDocument
- 仅支持文本型 PDF（A 股年报 99%+ 是交易所电子报送生成的文本型 PDF）
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class PDFParser:
    """封装 pdfplumber 解析逻辑"""

    def __init__(
        self,
        pdf_path: str,
        extract_tables: bool = True,
        password: Optional[str] = None,
    ):
        """
        Args:
            pdf_path: PDF 文件路径
            extract_tables: 是否提取表格
            password: PDF 密码（如有）
        """
        self.pdf_path = Path(pdf_path)
        self.extract_tables = extract_tables
        self.password = password

        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")
        if self.pdf_path.suffix.lower() != ".pdf":
            raise ValueError(f"不是 PDF 文件: {pdf_path}")

    def extract(self) -> dict:
        """解析 PDF，返回原始字典结构

        Returns:
            {
                "metadata": {"file_name": str, "page_count": int, ...},
                "pages": [{"page_num": int, "text": str, "tables": [...]}, ...],
            }
        """
        try:
            import pdfplumber
        except ImportError:
            raise ImportError(
                "pdfplumber 未安装。请执行: pip install pdfplumber"
            )

        metadata = {
            "file_name": self.pdf_path.name,
            "page_count": 0,
            "report_year": self._guess_year(),
            "extract_tool": "pdfplumber",
            "extract_date": datetime.now().isoformat(),
        }
        pages = []

        with pdfplumber.open(str(self.pdf_path), password=self.password) as pdf:
            metadata["page_count"] = len(pdf.pages)

            for page_num, page in enumerate(pdf.pages, start=1):
                # 提取文字
                text = page.extract_text() or ""

                # 提取表格
                tables = []
                if self.extract_tables:
                    raw_tables = page.extract_tables()
                    for table_idx, table in enumerate(raw_tables):
                        if table and len(table) > 1:  # 至少要有表头+数据行
                            tables.append({
                                "table_index": table_idx,
                                "page_number": page_num,
                                "rows": table,  # list[list[str]]
                                "row_count": len(table),
                                "col_count": len(table[0]) if table[0] else 0,
                            })

                pages.append({
                    "page_num": page_num,
                    "text": text,
                    "tables": tables,
                })

        logger.info(f"pdfplumber 解析完成: {metadata['page_count']} 页, "
                     f"{sum(len(p['tables']) for p in pages)} 个表格")

        return {"metadata": metadata, "pages": pages}

    def _guess_year(self) -> Optional[int]:
        """从文件名猜测年报年份

        常见命名模式：
        - 600519_2024.pdf → 2024
        - 贵州茅台2024年年报.pdf → 2024
        - 2024-12-31_annual_report.pdf → 2024
        """
        name = self.pdf_path.stem
        years = re.findall(r"(20\d{2})", name)
        if years:
            return int(years[-1])
        return None
