"""TOC 解析 + PDF 拆分 — 从年报目录找到章节边界，在 ≤200 页处拆分

设计约束:
- 只拆分一份 PDF 为最多 2 块（MinerU 免费版 200 页限制）
- 找到目录中最接近 200 页的章节号，从该章节起始页拆分
- 不引入新依赖：pdfplumber 提取文本，PyPDF2 切分 PDF
- 支持 "第X节"、"一、" 等中文目录格式
"""

import logging
import re
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# TOC 条目正则：匹配 "第X节 标题 ...... 页码" 或 "一、标题 ...... 页码"
_TOC_PATTERNS = [
    # 第X节 标题 ...... 页码
    re.compile(
        r"(第[一二三四五六七八九十]+节\s*[^\d\n]+?)[\s.．…]*?(\d{1,3})\s*$",
        re.MULTILINE,
    ),
    # 一、 二、 三、 ... 标题 ...... 页码
    re.compile(
        r"^([一二三四五六七八九十]+[、.][^\d\n]+?)[\s.．…]*?(\d{1,3})\s*$",
        re.MULTILINE,
    ),
    # 1. 2. 3. ... 标题 ...... 页码
    re.compile(
        r"^(\d+\.[\s][^\d\n]+?)[\s.．…]*?(\d{1,3})\s*$",
        re.MULTILINE,
    ),
]


class TOCSplitter:
    """从年报 PDF 提取目录，找到最接近 200 页的章节边界并拆分"""

    # 目录通常在前 15 页
    TOC_SEARCH_PAGES = 15

    def __init__(self, pdf_path: str | Path):
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")

    # ────────────────────────────────────────
    # 公开接口
    # ────────────────────────────────────────

    def split(self, max_pages: int = 200, output_dir: str | Path | None = None) -> list[str]:
        """提取 TOC → 找分界点 → 拆分 PDF

        Args:
            max_pages: MinerU 单次处理页数上限（默认 200）
            output_dir: 输出目录（默认 data/raw/）

        Returns:
            [chunk1_path, chunk2_path] — 若无需拆分则返回 [原始路径]
        """
        total = self._get_page_count()
        if total <= max_pages:
            logger.info(f"PDF 共 {total} 页，无需拆分")
            return [str(self.pdf_path)]

        toc = self._extract_toc()
        if not toc:
            logger.warning("未解析到目录条目，按 {max_pages} 页硬拆分")
            split_page = max_pages
        else:
            split_page = self._find_split_page(toc, total, max_pages)
            if split_page is None or split_page <= 1:
                logger.warning(
                    f"未找到合适章节边界，按 {max_pages} 页硬拆分"
                )
                split_page = max_pages

        out_dir = Path(output_dir) if output_dir else self.pdf_path.parent
        return self._split_pdf(split_page, out_dir)

    # ────────────────────────────────────────
    # 页数读取
    # ────────────────────────────────────────

    def _get_page_count(self) -> int:
        """读取 PDF 总页数"""
        from PyPDF2 import PdfReader
        reader = PdfReader(str(self.pdf_path))
        return len(reader.pages)

    # ────────────────────────────────────────
    # TOC 提取
    # ────────────────────────────────────────

    def _extract_toc(self) -> list[dict]:
        """用 pdfplumber 提取前 N 页文本，解析目录条目

        Returns:
            [{"chapter": "财务报告", "page": 94, "text": "第八节 财务报告"}, ...]
            按页码升序排列
        """
        import pdfplumber

        raw_entries: list[dict] = []

        try:
            with pdfplumber.open(str(self.pdf_path)) as pdf:
                max_page = min(self.TOC_SEARCH_PAGES, len(pdf.pages))
                for page_num in range(max_page):
                    text = pdf.pages[page_num].extract_text() or ""
                    # 跳过明显不包含目录的页面
                    if "目录" not in text and "目  录" not in text:
                        # 但 TOC 条目可能散落在随后的几页
                        # 只要本页有数字+章节关键词就继续解析
                        if page_num > 3 and not re.search(
                            r"[节章部篇]|[一二三四五六七八九十]+[、.]",
                            text,
                        ):
                            continue

                    page_entries = self._parse_toc_page(text, page_num + 1)
                    raw_entries.extend(page_entries)
        except Exception as e:
            logger.warning(f"TOC 提取失败: {e}")

        # 去重: 同章节名+同页码的只保留一条（防止跨页重复解析）
        seen: set[tuple[str, int]] = set()
        result: list[dict] = []
        for e in raw_entries:
            key = (e["chapter"], e["page"])
            if key not in seen and 1 <= e["page"] <= 500:
                seen.add(key)
                result.append(e)
        result.sort(key=lambda e: e["page"])

        logger.info(
            f"TOC 解析: 共 {len(result)} 个章节条目, "
            f"起始页范围 {result[0]['page'] if result else 'N/A'} - "
            f"{result[-1]['page'] if result else 'N/A'}"
        )
        return result

    def _parse_toc_page(self, text: str, page_num: int) -> list[dict]:
        """解析单页文本中的目录条目"""
        entries: list[dict] = []
        text = text.replace(" ", "").replace(" ", "")  # 去除空格
        for pattern in _TOC_PATTERNS:
            for match in pattern.finditer(text):
                chapter_name = match.group(1).strip()
                page_str = match.group(2).strip()
                try:
                    page = int(page_str)
                except ValueError:
                    continue
                # 过滤: 章节名至少 3 个字, 页码不能是当前页（目录页码=当前页不可信）
                if len(chapter_name) >= 3 and page != page_num:
                    entries.append({
                        "chapter": chapter_name,
                        "page": page,
                        "text": match.group(0).strip(),
                    })
        return entries

    # ────────────────────────────────────────
    # 拆分点查找
    # ────────────────────────────────────────

    def _find_split_page(
        self, toc: list[dict], total_pages: int, max_pages: int
    ) -> Optional[int]:
        """找到最接近 max_pages 且不超过 total_pages 的章节起始页

        策略: 从最后一个章节往前找，找到 ≤max_pages 的页。
        如果所有章节起始页都 > max_pages，取 max_pages。
        """
        # 从大到小遍历
        for entry in reversed(toc):
            pg = entry["page"]
            if pg <= max_pages:
                # 找到第一个 ≤max_pages 的章节（最接近 max_pages）
                if pg >= 5:  # 跳过首页等太靠前的
                    logger.info(
                        f"选择拆分章节: [{entry['chapter']}] 起始页={pg}"
                    )
                    return pg

        # 没有合适的章节: 返回 max_pages 硬拆分
        if max_pages < total_pages:
            return max_pages
        return None

    # ────────────────────────────────────────
    # PDF 拆分
    # ────────────────────────────────────────

    def _split_pdf(
        self, split_page: int, output_dir: Path
    ) -> list[str]:
        """在 split_page 处拆分 PDF

        命名: {原始文件名}-1.pdf, {原始文件名}-2.pdf
        第1块: 1 到 split_page-1
        第2块: split_page 到末尾
        """
        from PyPDF2 import PdfReader, PdfWriter

        reader = PdfReader(str(self.pdf_path))
        total = len(reader.pages)
        stem = self.pdf_path.stem

        output_dir.mkdir(parents=True, exist_ok=True)
        out_paths: list[str] = []

        # 第1块: pages 0 到 split_page-1 (0-indexed)
        if split_page > 1:
            writer = PdfWriter()
            for i in range(split_page - 1):
                writer.add_page(reader.pages[i])
            path1 = str(output_dir / f"{stem}-1.pdf")
            with open(path1, "wb") as f:
                writer.write(f)
            out_paths.append(path1)
            logger.info(f"第1块: 页 1-{split_page - 1} -> {path1}")

        # 第2块: pages split_page-1 到末尾
        writer = PdfWriter()
        for i in range(split_page - 1, total):
            writer.add_page(reader.pages[i])
        path2 = str(output_dir / f"{stem}-2.pdf")
        with open(path2, "wb") as f:
            writer.write(f)
        out_paths.append(path2)
        logger.info(f"第2块: 页 {split_page}-{total} -> {path2}")

        return out_paths
