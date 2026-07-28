"""第0层：文档切分器 — 将PDF原始提取结果切分为四大区块

切分策略（按优先级）：
1. 基于章节标题关键词匹配（主路径）
2. 基于页码范围估算（降级方案，适用于标题不标准的年报）

四大区块及流向：
- financial_data    → B 层（提取数值）
- management_discussion → D 层（查找解释）
- footnotes         → D 层（查找附注）
- company_overview  → A 层（打标签）

设计约束：
- 不涉及 LLM，纯规则引擎
- 所有正则可配置
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class DocumentChunker:
    """根据章节标题规则，将提取结果切分为四大区块"""

    # 章节标题关键词（按优先级排列，越靠前越可靠）
    SECTION_PATTERNS = {
        "financial_data": [
            # 三大报表的明确标记
            r"合并资产负债表",
            r"资产负债表",
            r"合并利润表",
            r"利润表",
            r"合并现金流量表",
            r"现金流量表",
            r"财务报表(?!附注)",  # "财务报表"但不包括"财务报表附注"
            r"审计报告",
        ],
        "management_discussion": [
            r"管理层讨论与分析",
            r"经营情况讨论与分析",
            r"董事会报告",
            r"管理层报告",
            r"经营情况回顾",
        ],
        "footnotes": [
            r"财务报表附注",
            r"会计报表附注",
            r"附注",
            r"财务报告说明",
        ],
        "company_overview": [
            r"公司简介",
            r"公司基本情况",
            r"公司信息",
            r"释义",
            r"主要会计数据",
        ],
    }

    # 用于从文本中提取公司名和股票代码
    COMPANY_NAME_PATTERNS = [
        r"(?:公司名称|公司全称|发行人名称)[：:]\s*(.+?)(?:\n|$)",
        r"(.+?)股份有限公司",
    ]
    STOCK_CODE_PATTERNS = [
        r"(?:股票代码|证券代码|股票简称)[：:]\s*(\d{6})",
        r"代码[：:]\s*(\d{6})",
    ]
    INDUSTRY_PATTERNS = [
        r"(?:所属行业|行业分类|行业类别)[：:]\s*(.+?)(?:\n|$)",
    ]

    def __init__(self, raw_data: dict):
        """
        Args:
            raw_data: PDFParser.extract() 的输出
                      {"metadata": {...}, "pages": [{"page_num": int, "text": str, "tables": [...]}]}
        """
        self.raw_data = raw_data
        self.pages = raw_data.get("pages", [])
        self.metadata = raw_data.get("metadata", {})

    def chunk(self) -> dict:
        """执行切分，返回符合 RawDocument schema 的四大区块

        Returns:
            dict 可直接用于 RawDocument(**chunked)
        """
        # 合并所有页面的文本为一个字符串（保留页码标记）
        full_text = self._build_full_text()
        # 按章节标题切分页码范围
        page_ranges = self._identify_sections(full_text)

        # 切分四大区块
        financial_data = self._extract_financial_data(page_ranges.get("financial_data"))
        management_discussion = self._extract_management_discussion(
            page_ranges.get("management_discussion")
        )
        footnotes = self._extract_footnotes(page_ranges.get("footnotes"))
        company_overview = self._extract_company_overview(
            page_ranges.get("company_overview"), full_text
        )

        return {
            "metadata": self._build_metadata(),
            "financial_data": financial_data,
            "management_discussion": management_discussion,
            "footnotes": footnotes,
            "company_overview": company_overview,
        }

    # ────────────────────────────────────────
    # 全文本构建
    # ────────────────────────────────────────

    def _build_full_text(self) -> str:
        """将所有页的文本合并为一个带页码标记的完整文本"""
        lines = []
        for page in self.pages:
            page_num = page.get("page_num", 0)
            text = page.get("text", "")
            lines.append(f"\n[PAGE_{page_num}]\n{text}")
        return "\n".join(lines)

    # ────────────────────────────────────────
    # 章节识别
    # ────────────────────────────────────────

    def _identify_sections(self, full_text: str) -> dict[str, tuple[int, int]]:
        """根据章节标题关键词，识别各区块的起始和结束页码

        Returns:
            {"financial_data": (start_page, end_page), ...}
            end_page 为 None 表示延续到文档末尾
        """
        # 按页码分段
        page_blocks = re.split(r'\[PAGE_(\d+)\]', full_text)

        # 构建 (page_num, text) 对
        page_texts: list[tuple[int, str]] = []
        for i in range(1, len(page_blocks), 2):
            page_num = int(page_blocks[i])
            text = page_blocks[i + 1] if i + 1 < len(page_blocks) else ""
            page_texts.append((page_num, text))

        # 对每个区块类型查找匹配的起始页
        ranges: dict[str, Optional[tuple[int, int]]] = {}
        found_starts: list[tuple[str, int]] = []  # [(section, page_num), ...]

        for section, patterns in self.SECTION_PATTERNS.items():
            for page_num, text in page_texts:
                for pattern in patterns:
                    if re.search(pattern, text):
                        found_starts.append((section, page_num))
                        break
                else:
                    continue
                break  # 找到一个匹配就跳出，取最早的

        # 按页码排序
        found_starts.sort(key=lambda x: x[1])

        # 确定每个区块的起止页
        section_order = ["company_overview", "financial_data", "management_discussion", "footnotes"]
        last_page = max(p for p, _ in page_texts) if page_texts else 0

        for i, (section, start_page) in enumerate(found_starts):
            if i + 1 < len(found_starts):
                end_page = found_starts[i + 1][1] - 1
            else:
                end_page = last_page
            ranges[section] = (start_page, end_page)

        # 对未找到的区块做降级处理
        for section in section_order:
            if section not in ranges:
                ranges[section] = self._fallback_range(section, page_texts)

        return {k: v for k, v in ranges.items() if v is not None}

    def _fallback_range(self, section: str, page_texts: list) -> Optional[tuple[int, int]]:
        """当关键词匹配失败时的降级策略"""
        total_pages = len(page_texts)
        if total_pages == 0:
            return None

        first_page = page_texts[0][0]
        last_page = page_texts[-1][0]

        # 经验规则：
        if section == "company_overview":
            return (first_page, min(first_page + 10, last_page))
        elif section == "financial_data":
            # 财报通常在中间偏前
            mid = total_pages // 3
            return (page_texts[mid][0], page_texts[min(mid + 30, total_pages - 1)][0])
        elif section == "management_discussion":
            mid = total_pages // 2
            return (page_texts[mid][0], page_texts[min(mid + 40, total_pages - 1)][0])
        elif section == "footnotes":
            # 附注通常在最后
            start = max(0, total_pages - total_pages // 3)
            return (page_texts[start][0], last_page)
        return (first_page, last_page)

    # ────────────────────────────────────────
    # 四大区块提取
    # ────────────────────────────────────────

    def _extract_financial_data(
        self, page_range: Optional[tuple[int, int]]
    ) -> dict:
        """提取财务数据（三大报表原始 OCR 表格）

        Returns:
            {"balance_sheet": [RawTableRow], "income_statement": [...], "cashflow_statement": [...]}
        """
        tables = self._get_tables_in_range(page_range)

        balance_sheet = []
        income_statement = []
        cashflow_statement = []

        for table in tables:
            page_num = table.get("page_number", 0)
            rows = table.get("rows", [])
            if not rows or len(rows) < 2:
                continue

            # 根据表头关键词分类
            header_text = " ".join(str(c) for c in rows[0] if c).lower()
            typed_rows = [
                {
                    "row_index": i,
                    "columns": {f"col_{j}": str(c) for j, c in enumerate(row) if c},
                    "page_number": page_num,
                }
                for i, row in enumerate(rows)
            ]

            if any(kw in header_text for kw in ["资产", "负债", "所有者权益", "balance sheet"]):
                balance_sheet.extend(typed_rows)
            elif any(kw in header_text for kw in ["利润", "收入", "成本", "费用", "income"]):
                income_statement.extend(typed_rows)
            elif any(kw in header_text for kw in ["现金", "cash flow", "流量"]):
                cashflow_statement.extend(typed_rows)
            else:
                # 无法判断的归入资产负债表（最常见）
                balance_sheet.extend(typed_rows)

        return {
            "balance_sheet": balance_sheet,
            "income_statement": income_statement,
            "cashflow_statement": cashflow_statement,
        }

    def _extract_management_discussion(
        self, page_range: Optional[tuple[int, int]]
    ) -> dict:
        """提取管理层讨论与分析"""
        if page_range is None:
            return {"sections": []}

        start, end = page_range
        sections = []

        for page in self.pages:
            page_num = page.get("page_num", 0)
            if page_num < start or page_num > end:
                continue

            text = page.get("text", "")
            # 按常见小节标题切分
            subsections = re.split(
                r'\n(?=(?:[一二三四五六七八九十]+[、.]|'
                r'(?:\d+[\.、])|'
                r'(?:（[一二三四五六七八九十]+）)|'
                r'(?:[A-Z][一-鿿]+[：:])|'
                r'[\(（]\d+[\)）]))',
                text,
            )

            current_title = "管理层讨论"
            for sub in subsections:
                sub = sub.strip()
                if not sub:
                    continue
                # 前 30 个字符作为标题
                title = sub[:40].replace("\n", " ")
                sections.append({
                    "title": title,
                    "content": sub,
                    "page_number": page_num,
                })

        return {"sections": sections}

    def _extract_footnotes(
        self, page_range: Optional[tuple[int, int]]
    ) -> dict:
        """提取附注明细"""
        if page_range is None:
            return {"items": []}

        start, end = page_range
        items = []

        for page in self.pages:
            page_num = page.get("page_num", 0)
            if page_num < start or page_num > end:
                continue

            text = page.get("text", "")
            # 附注通常有编号，如 "1." "（一）" "注1"
            note_pattern = re.compile(
                r'(?:^|\n)((?:注\d+|附注[一二三四五六七八九十\d]+|'
                r'[\(（][一二三四五六七八九十\d]+[\)）]|'
                r'\d+\.[  ]+[^\d])[^\n]*)',
                re.MULTILINE,
            )
            matches = note_pattern.findall(text)

            for i, match in enumerate(matches[:3]):  # 每页最多3条
                name = match[:30].strip()
                if len(name) > 3:  # 过滤明显的误匹配
                    # 提取该附注涉及的所有表格
                    is_table = any(
                        kw in match
                        for kw in ["表", "明细", "构成", "变动", "情况"]
                    )
                    items.append({
                        "name": name,
                        "content": match[:2000],  # 截断长文本
                        "page_number": page_num,
                        "is_table": is_table,
                    })

        return {"items": items}

    def _extract_company_overview(
        self, page_range: Optional[tuple[int, int]], full_text: str
    ) -> dict:
        """提取公司基本情况"""
        company_name = self._regex_extract(full_text, self.COMPANY_NAME_PATTERNS)
        stock_code = self._regex_extract(full_text, self.STOCK_CODE_PATTERNS)
        industry = self._regex_extract(full_text, self.INDUSTRY_PATTERNS)

        # 合并 overview 区间的文本作为业务描述
        business_desc = ""
        if page_range:
            start, end = page_range
            business_desc = "\n".join(
                p.get("text", "")
                for p in self.pages
                if start <= p.get("page_num", 0) <= end
            )[:3000]

        return {
            "company_name": company_name or "",
            "stock_code": stock_code or "",
            "business_description": business_desc,
            "industry_classification": industry or "",
        }

    # ────────────────────────────────────────
    # 工具方法
    # ────────────────────────────────────────

    def _get_tables_in_range(
        self, page_range: Optional[tuple[int, int]]
    ) -> list[dict]:
        """获取指定页码范围的所有表格"""
        if page_range is None:
            return []
        start, end = page_range
        tables = []
        for page in self.pages:
            page_num = page.get("page_num", 0)
            if start <= page_num <= end:
                tables.extend(page.get("tables", []))
        return tables

    def _build_metadata(self) -> dict:
        """构建 DocumentMetadata"""
        meta = self.metadata
        return {
            "file_name": meta.get("file_name", ""),
            "page_count": meta.get("page_count", 0),
            "report_year": meta.get("report_year"),
            "extract_tool": meta.get("extract_tool", "pdfplumber"),
            "extract_date": meta.get("extract_date", ""),
        }

    @staticmethod
    def _regex_extract(text: str, patterns: list[str]) -> Optional[str]:
        """尝试一组正则，返回第一个 match"""
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()
        return None
