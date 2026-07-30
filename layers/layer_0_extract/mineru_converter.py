"""MinerU JSON 转换器 — 将 MinerU 平板 content_list 转为页格式

职责:
1. 按 page_idx 分组 MinerU 元素
2. 提取文本拼接为逐页文本
3. 解析 HTML table 为 RawTableRow 格式
4. 输出兼容 pdfplumber/chunker 的标准 pages 结构
"""

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class MineruConverter:
    """将 MinerU content_list 转为标准 page-based dict"""

    def to_pages_format(
        self,
        content_list: list[dict],
        file_name: str = "",
        page_count: int = 0,
    ) -> dict:
        """MinerU content_list → {"metadata": ..., "pages": [...]}

        Args:
            content_list: MinerU JSON 的 content_list 数组
            file_name: 原始 PDF 文件名
            page_count: 原始 PDF 总页数

        Returns:
            与 pdfplumber 兼容的 page-based dict:
            {"metadata": {...}, "pages": [{"page_num": int, "text": str, "tables": [...]}]}
        """
        # 1. 按 page_idx 分组
        page_map: dict[int, dict] = {}

        for item in content_list:
            if not isinstance(item, dict):
                continue
            page_idx = item.get("page_idx", 0)
            if page_idx not in page_map:
                page_map[page_idx] = {
                    "page_num": page_idx,
                    "text_parts": [],
                    "tables": [],
                }

            elem_type = item.get("type", "")
            if elem_type in ("text", "header"):
                text = item.get("text", "")
                if text:
                    page_map[page_idx]["text_parts"].append(text)
            elif elem_type == "table":
                html = item.get("table_body", "")
                if html:
                    rows = self._parse_html_table(html)
                    if rows:
                        page_map[page_idx]["tables"].append({
                            "table_index": len(page_map[page_idx]["tables"]),
                            "page_number": page_idx,
                            "rows": rows,
                            "row_count": len(rows),
                            "col_count": max((len(r) for r in rows), default=0),
                        })

        # 2. 构建 pages 列表（按页码排序）
        pages = []
        for page_num in sorted(page_map.keys()):
            page_data = page_map[page_num]
            text = "\n".join(page_data["text_parts"])
            pages.append({
                "page_num": page_num,
                "text": text,
                "tables": page_data["tables"],
            })

        if not pages:
            logger.warning("MinerU 转换: content_list 为空，无法构建页面数据")

        # 3. 构建 metadata
        metadata = {
            "file_name": file_name or "",
            "page_count": page_count or (max(page_map.keys()) if page_map else 0),
            "report_year": self._guess_year(file_name),
            "extract_tool": "mineru",
            "extract_date": "",
            "mineru_stats": {
                "total_elements": len(content_list),
                "total_pages": len(page_map),
                "total_tables": sum(len(p["tables"]) for p in page_map.values()),
            },
        }

        logger.info(
            f"MinerU 转换完成: {metadata['page_count']} 页, "
            f"{len(pages)} 个有效页, "
            f"{metadata['mineru_stats']['total_tables']} 张表"
        )

        return {"metadata": metadata, "pages": pages}

    # ────────────────────────────────────────
    # HTML 表格解析
    # ────────────────────────────────────────

    def _parse_html_table(self, html: str) -> list[list[str]]:
        """解析 MinerU 的 HTML <table> 为行列结构

        MinerU 输出格式:
          <table><tr><td>项目</td><td>金额</td></tr>...</table>

        Returns:
            [["项目", "金额"], ["货币资金", "123,456.78"], ...]
            空结果返回 []
        """
        rows: list[list[str]] = []

        # 提取所有 <tr>...</tr> 内容
        tr_pattern = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
        # 提取 <td> 或 <th> 内容
        cell_pattern = re.compile(
            r"<(?:td|th)[^>]*>(.*?)</(?:td|th)>", re.DOTALL | re.IGNORECASE
        )

        for tr_match in tr_pattern.finditer(html):
            tr_content = tr_match.group(1)
            cells: list[str] = []
            for cell_match in cell_pattern.finditer(tr_content):
                cell_html = cell_match.group(1)
                # 清洗: 去除内部 HTML 标签，保留文本
                cell_text = self._clean_cell_text(cell_html)
                cells.append(cell_text)
            if cells:
                rows.append(cells)

        return rows

    @staticmethod
    def _clean_cell_text(html_fragment: str) -> str:
        """清洗单元格 HTML，提取纯文本

        处理:
        - <sup>/<sub>/<span>/<p> 等标签
        - &nbsp; 等 HTML 实体
        """
        text = html_fragment
        # 移除所有 HTML 标签
        text = re.sub(r"<[^>]+>", "", text)
        # HTML 实体解码
        text = text.replace("&nbsp;", " ")
        text = text.replace("&amp;", "&")
        text = text.replace("&lt;", "<")
        text = text.replace("&gt;", ">")
        text = text.replace("&quot;", '"')
        text = text.replace("&#160;", " ")
        # 合并空白
        text = re.sub(r"\s+", " ", text).strip()
        return text

    # ────────────────────────────────────────
    # 工具方法
    # ────────────────────────────────────────

    @staticmethod
    def _guess_year(file_name: str) -> Optional[int]:
        """从文件名猜测年报年份"""
        years = re.findall(r"(20\d{2})", file_name)
        if years:
            return int(years[-1])
        return None
