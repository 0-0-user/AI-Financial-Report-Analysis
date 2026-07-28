"""第0层：PDF解析器 — 将年报PDF提取为原始文本和表格数据

支持两种后端：
- pdfplumber（轻量、纯 Python、适合文本型 PDF）
- MinerU（重量级、需 GPU、适合扫描件/复杂表格——外部工具，需单独安装）

设计约束：
- 不涉及任何业务逻辑，只做文件→结构化数据
- 输出原始 dict，由 chunker.py 切分为 RawDocument
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class PDFParser:
    """封装 PDF 解析逻辑，支持多种后端"""

    def __init__(
        self,
        pdf_path: str,
        backend: str = "pdfplumber",
        extract_tables: bool = True,
        password: Optional[str] = None,
    ):
        """
        Args:
            pdf_path: PDF 文件路径
            backend: "pdfplumber" | "mineru"
            extract_tables: 是否提取表格
            password: PDF 密码（如有）
        """
        self.pdf_path = Path(pdf_path)
        self.backend = backend
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
        if self.backend == "pdfplumber":
            return self._extract_with_pdfplumber()
        elif self.backend == "mineru":
            return self._extract_with_mineru()
        else:
            raise ValueError(f"不支持的后端: {self.backend}，可选: pdfplumber, mineru")

    # ────────────────────────────────────────
    # pdfplumber 后端（推荐，轻量级）
    # ────────────────────────────────────────

    def _extract_with_pdfplumber(self) -> dict:
        """使用 pdfplumber 逐页提取文字和表格"""
        try:
            import pdfplumber
        except ImportError:
            raise ImportError(
                "pdfplumber 未安装。请执行: pip install pdfplumber\n"
                "或切换后端: parser = PDFParser(path, backend='mineru')"
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

    # ────────────────────────────────────────
    # MinerU 后端（外部工具，处理扫描件）
    # ────────────────────────────────────────

    def _extract_with_mineru(self) -> dict:
        """使用 MinerU 命令行工具解析 PDF

        MinerU 是开源项目（github.com/opendatalab/MinerU），
        需要单独安装和配置。这里通过 subprocess 调用其 CLI。

        输出格式：MinerU 生成 JSON 文件，每页一个，
        包含 markdown 文本、表格、图片等信息。
        """
        import subprocess
        import json
        import tempfile

        output_dir = Path(tempfile.mkdtemp(prefix="mineru_output_"))

        try:
            # 调用 MinerU CLI：magic-pdf -p <pdf_path> -o <output_dir>
            result = subprocess.run(
                ["magic-pdf", "-p", str(self.pdf_path), "-o", str(output_dir)],
                capture_output=True,
                text=True,
                timeout=600,  # 大文件可能需要更长时间
            )

            if result.returncode != 0:
                raise RuntimeError(f"MinerU 解析失败: {result.stderr}")

            # 读取 MinerU 输出的 JSON
            json_dir = output_dir / self.pdf_path.stem / "auto"
            if not json_dir.exists():
                # 尝试其他可能的输出路径
                candidates = list(output_dir.glob("**/*.json"))
                json_dir = candidates[0].parent if candidates else output_dir

            pages = []
            json_files = sorted(json_dir.glob("*.json"))

            for jf in json_files:
                with open(jf, encoding="utf-8") as f:
                    page_data = json.load(f)

                # MinerU 输出格式 → 统一格式
                pages.append({
                    "page_num": page_data.get("page_num", 0),
                    "text": page_data.get("markdown", page_data.get("text", "")),
                    "tables": page_data.get("tables", []),
                })

            metadata = {
                "file_name": self.pdf_path.name,
                "page_count": len(pages),
                "report_year": self._guess_year(),
                "extract_tool": "mineru",
                "extract_date": datetime.now().isoformat(),
            }

            logger.info(f"MinerU 解析完成: {metadata['page_count']} 页")
            return {"metadata": metadata, "pages": pages}

        finally:
            # 清理临时目录
            import shutil
            if output_dir.exists():
                shutil.rmtree(output_dir, ignore_errors=True)

    # ────────────────────────────────────────
    # 工具方法
    # ────────────────────────────────────────

    def _guess_year(self) -> Optional[int]:
        """从文件名猜测年报年份

        常见命名模式：
        - 600519_2024.pdf → 2024
        - 贵州茅台2024年年报.pdf → 2024
        - 2024-12-31_annual_report.pdf → 2024
        """
        import re
        name = self.pdf_path.stem
        # 匹配 4 位数字（优先取 20xx）
        years = re.findall(r"(20\d{2})", name)
        if years:
            return int(years[-1])  # 取最后一个年份
        return None
