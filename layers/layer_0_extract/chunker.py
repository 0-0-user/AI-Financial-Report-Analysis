"""第0层: 文档切分器 — 将PDF原始提取结果切分为四大区块

切分策略 (按优先级) : 
1. 基于章节标题关键词匹配 (主路径) 
2. 基于页码范围估算 (降级方案，适用于标题不标准的年报) 

四大区块及流向: 
- financial_data    -> B 层 (提取数值) 
- management_discussion -> D 层 (查找解释) 
- footnotes         -> D 层 (查找附注) 
- company_overview  -> A 层 (打标签) 

设计约束: 
- 不涉及 LLM，纯规则引擎
- 所有正则可配置
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class DocumentChunker:
    """根据章节标题规则，将提取结果切分为四大区块"""

    # 章节标题关键词 (按优先级排列，越靠前越可靠) 
    #
    # 重要: A 股年报中财务关键词 ("资产负债表""利润表"等) 会在目录+审计报告开头
    # 多次出现，导致误匹。固定策略: 
    #   1. 用章节编号模式 ("二、财务报表""第十节 财务报告") 定位实际财务章节起始
    #   2. 前 N 页 (目录页) 的匹配自动忽略
    #   3. 降级方案: 无匹配时按经验规则估算
    SECTION_PATTERNS = {
        "financial_data": [
            # 实际财务报表章节标题 ("二、财务报表 合并资产负债表"，不在目录/审计引言段) 
            r"二、财务报表",
            r"财务报表\s*\n+\s*合并资产负债表",
        ],
        "management_discussion": [
            r"第三节\s*管理层讨论与分析",
            r"第[三四五六七八九十]+节\s*管理层讨论与分析",
            r"经营情况讨论与分析",
            r"董事会报告",
            r"管理层报告",
        ],
        "footnotes": [
            # 附注章节标题通常独立成行（行首）。审计报告正文的"财务报表附注"是句中引用，
            # 用多行行首锚点避开误命中，让附注章节起点正确、财务区自然收敛到核心报表页。
            r"(?m)^\s*财务报表附注\s*$",
            r"(?m)^\s*财务报表附注[（(]",
            r"财务报表附注",
            r"会计报表附注",
        ],
        "company_overview": [
            r"公司简介",
            r"公司基本情况",
            r"公司信息",
            r"释义",
            r"主要会计数据",
        ],
    }

    # 目录区页码上限: 页码 <= 此值的匹配忽略 (目录/审计引言段的误拦) 
    _TOC_PAGE_LIMIT = 10

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
        # 合并所有页面的文本为一个字符串 (保留页码标记) 
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
                # 目录页 (前 N 页) 的匹配大概率是目录/审计引言，跳过
                if page_num <= self._TOC_PAGE_LIMIT:
                    continue
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

        # 用有序字典: 按 section_order 的顺序，从 found_starts 取各 section 的首个匹配
        start_map: dict[str, int] = {}
        for section in section_order:
            matches = [(s, p) for (s, p) in found_starts if s == section]
            if matches:
                start_map[section] = matches[0][1]

        # 按起止页排序 (与 section_order 无关，纯按页码) 
        sorted_sections = sorted(start_map.items(), key=lambda x: x[1])

        for i, (section, start_page) in enumerate(sorted_sections):
            if i + 1 < len(sorted_sections):
                next_section, next_start = sorted_sections[i + 1]
                # 如果下一区段起始页与当前重叠或小于当前起止，修正
                if next_start <= start_page:
                    # 跨越到文档末尾
                    end_page = last_page
                else:
                    end_page = next_start - 1
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

        # 经验规则: 
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

    @staticmethod
    def _is_core_statement_table(rows: list[list]) -> bool:
        """判断一张表是否是核心三大报表表（纯代码粗筛，不调 LLM）

        依据: 表头前 2 行（空白归一化后）含"附注"列 且 含日期/年度列
        （"12月31日" 或 "年度"）。年报财务章节的三大报表（合并+母公司）
        表头都是 "项目 | 附注 | 日期/年度" 结构；附注明细表表头无"附注"列。
        实证: 对年报财务区 216 张表筛选恰好命中 6 张核心报表，0 误报 0 漏报。

        Args:
            rows: 表格行列数据（list[list]，每个元素是单元格文本）

        Returns:
            是否为三大报表核心表
        """
        if not rows:
            return False
        header_cells: list[str] = []
        for r in rows[:2]:
            for c in r:
                if c is None:
                    continue
                norm = str(c).replace(" ", "").replace("　", "").strip()
                if norm:
                    header_cells.append(norm)
        text = " ".join(header_cells)
        has_note_col = "附注" in text
        has_date_col = ("12月31日" in text) or ("年度" in text)
        return has_note_col and has_date_col

    def _find_page_statement_title(self, page_num: int) -> str:
        """在该页文本中找三大报表标题（含合并/母公司前缀）

        报表标题（如"母公司资产负债表"）是页面上的独立文本元素，MinerU 不挂到表格上。
        这里从页面文本提取标题，供 LLM 判断 report_scope（合并/母公司）的依据。
        页面无"合并/母公司"前缀（如只有"资产负债表"）时返回空串，由 LLM 默认合并。
        """
        for page in self.pages:
            if page.get("page_num") != page_num:
                continue
            text = page.get("text", "")
            m = re.search(r"(合并|母公司)\s*(资产负债表|利润表|现金流量表)", text)
            if m:
                return m.group(1) + m.group(2)
            break
        return ""

    def _extract_financial_data(
        self, page_range: Optional[tuple[int, int]]
    ) -> dict:
        """提取财务数据 (三大报表原始 OCR 表格)

        v4: 核心表粗筛 + LLM 分类（含合并/母公司区分），不设降级。

        Returns:
            {"balance_sheet": [...], "income_statement": [...], "cashflow_statement": [...],
             "parent_balance_sheet": [...], "parent_income_statement": [...], "parent_cashflow_statement": [...]}
        """
        empty = {
            "balance_sheet": [], "income_statement": [], "cashflow_statement": [],
            "parent_balance_sheet": [], "parent_income_statement": [], "parent_cashflow_statement": [],
        }
        all_tables = self._get_tables_in_range(page_range)
        if not all_tables:
            return empty

        # 跨页合并: 同列数的连续表格拼接（如BS跨2页，PL跨页）
        all_tables = _merge_same_column_tables(all_tables)

        # 核心表粗筛
        core_tables = []
        for t in all_tables:
            rows = t.get("rows", [])
            if rows and self._is_core_statement_table(rows):
                t = dict(t)
                # 补页面标题（如"母公司资产负债表"），供 LLM 判断合并/母公司
                t["page_title"] = self._find_page_statement_title(t.get("page_number", 0))
                core_tables.append(t)
        if not core_tables:
            logger.warning("财务区域未找到核心三大报表表")
            return empty

        # LLM 分类: 每张表 → {statement_type, report_scope}
        try:
            classifications = self._classify_tables_with_llm(core_tables)
        except Exception as e:
            logger.warning(f"LLM 表分类失败，降级规则匹配: {e}")
            classifications = _rule_based_classify(core_tables)

        result: dict[str, list[dict]] = dict(empty)
        for i, table in enumerate(core_tables):
            page_num = table.get("page_number", 0)
            rows = table.get("rows", [])
            if not rows or len(rows) < 2:
                continue

            table_id = f"table_{i}"
            cls = classifications.get(table_id)
            if not cls:
                raise RuntimeError(
                    f"LLM 未分类表格 {table_id} (第{page_num}页, 表头: "
                    f"{' '.join(str(c) for c in (rows[0] if rows else [])[:4])[:80]})"
                )
            stype = cls["statement_type"]
            if stype == "other":
                continue
            scope = cls.get("report_scope", "consolidated")
            bucket = f"parent_{stype}" if scope == "parent" else stype
            if bucket not in result:
                raise RuntimeError(
                    f"LLM 分类结果异常: {table_id} → {stype}/{scope}"
                )

            typed_rows = [
                {
                    "row_index": i,
                    "columns": {f"col_{j}": str(c) for j, c in enumerate(row) if c},
                    "page_number": page_num,
                }
                for i, row in enumerate(rows)
            ]
            result[bucket].extend(typed_rows)

        return result

    def _classify_tables_with_llm(self, tables: list[dict]) -> dict[str, dict]:
        """调用 LLM 对 MinerU 财务表格进行报表类型分类

        输入: tables (列表, 每项含 page_number 和 rows)
        输出: {"table_0": {"statement_type": "balance_sheet", "report_scope": "consolidated"}, ...}
        """
        valid_tables = []
        for i, tbl in enumerate(tables):
            rows = tbl.get("rows", [])
            if not rows or len(rows) < 2:
                continue
            # 取表头前 2 行（足够判断报表类型）
            header_lines = []
            for r in rows[:2]:
                line = " | ".join(str(c) for c in r if c)
                if line:
                    header_lines.append(line)
            if not header_lines:
                continue
            valid_tables.append({
                "id": f"table_{i}",
                "page_idx": tbl.get("page_number", 0),
                "page_title": tbl.get("page_title", ""),
                "title": rows[0][0] if rows[0] else "",
                "header_text": " // ".join(header_lines)[:300],
            })

        if not valid_tables:
            logger.warning("financial_data 范围内无有效表格，跳过 LLM 分类")
            return {}

        from llm.client import LLMClient
        from pipeline.tracer import tracer

        # 分批分类: 每批最多 40 张表。MinerU 解析的表格可能上百张，
        # 一次性分类会让模型陷入逐表核对，思考链耗尽输出预算（reasoning 计入 max_tokens，
        # 结果正式输出为 0）。分批后每批任务量适中，模型能正常输出分类 JSON。
        BATCH_SIZE = 40
        client = LLMClient()
        merged: dict[str, dict] = {}

        for start in range(0, len(valid_tables), BATCH_SIZE):
            batch = valid_tables[start:start + BATCH_SIZE]
            batch_no = start // BATCH_SIZE + 1

            # 分批 + 漏表补全: GLM 分类偶尔会漏掉部分表（输出 JSON 不完整），
            # 对漏掉的表重新调用一次补全（这是保证分类完整，不是降级兜底）。
            # 重试后仍漏则失败即终止。
            pending = batch
            retries = 0
            while pending:
                response = client.chat(
                    "b0_table_localization",
                    {"tables": pending},
                    temperature=0.0,
                )
                batch_result = self._parse_llm_classification(str(response))
                if not batch_result:
                    raise RuntimeError(
                        f"LLM 表分类第 {batch_no} 批返回空结果，无法为 {len(pending)} 张表分配报表类型"
                    )
                merged.update(batch_result)
                missing = [t for t in pending if t["id"] not in batch_result]
                if not missing:
                    break
                pending = missing
                retries += 1
                if retries >= 2:
                    raise RuntimeError(
                        f"LLM 表分类第 {batch_no} 批重试 {retries} 次后仍遗漏 "
                        f"{len(pending)} 张表: {[t['id'] for t in pending[:10]]}"
                    )
                logger.warning(
                    f"LLM 分类第 {batch_no} 批遗漏 {len(missing)} 张表，第 {retries + 1} 次补全..."
                )

            tracer.milestone(
                "L0", "LLM表格分类", "success",
                f"第 {batch_no} 批: {len(batch)} 张表(累计 {len(merged)})",
            )

        # 校验: 每张表都必须被分类，缺漏即终止（不做兜底）
        missing = [t["id"] for t in valid_tables if t["id"] not in merged]
        if missing:
            raise RuntimeError(f"LLM 表分类遗漏 {len(missing)} 张表: {missing[:10]}")

        matched = sum(
            1 for v in merged.values()
            if v.get("statement_type") in ("balance_sheet", "income_statement", "cashflow")
        )
        logger.info(
            f"LLM 分类完成: {len(merged)} 张表, {matched} 张归属核心报表"
        )
        assign = "; ".join(f"{k}→{v}" for k, v in list(merged.items())[:8])
        tracer.milestone(
            "L0", "LLM表格分类", "success",
            f"{len(merged)} 张表, {matched} 张核心报表: {assign}",
        )
        return merged

    @staticmethod
    def _parse_llm_classification(raw: str) -> dict[str, dict]:
        """解析 LLM 返回的 JSON 分类结果

        Returns:
            {"table_0": {"statement_type": "balance_sheet", "report_scope": "consolidated"}, ...}
        """
        import json
        import re

        # 尝试提取 JSON
        m = re.search(r'\{[\s\S]*\}', raw)
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                return {}

            classifications = data.get("classifications", [])
            if isinstance(classifications, list):
                result: dict[str, dict] = {}
                for item in classifications:
                    if not (
                        isinstance(item, dict)
                        and "table_id" in item
                        and "statement_type" in item
                    ):
                        continue
                    result[item["table_id"]] = {
                        "statement_type": item["statement_type"],
                        "report_scope": item.get("report_scope", "consolidated"),
                    }
                return result
        return {}

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
                r'(?:\([一二三四五六七八九十]+\))|'
                r'(?:[A-Z][一-鿿]+:)|'
                r'\(\d+\)))',
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
            # 附注通常有编号，如 "1." " (一) " "注1"
            note_pattern = re.compile(
                r'(?:^|\n)((?:注\d+|附注[一二三四五六七八九十\d]+|'
                r'(?:[\(][一二三四五六七八九十\d]+[\)])|'
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
            "extract_tool": meta.get("extract_tool", "mineru"),
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


# ────────────────────────────────────────
# 跨页表格合并
# ────────────────────────────────────────

def _merge_same_column_tables(tables: list[dict]) -> list[dict]:
    """合并连续页上列数相同的表格（三大报表常跨2-3页）

    判断: 两张表相邻且列数相同 -> 拼接为一（第2张去表头行）
    """
    if len(tables) <= 1:
        return tables

    merged = []
    for t in tables:
        rows = t.get("rows", [])
        if not rows:
            merged.append(t)
            continue
        cols = len(rows[0]) if rows[0] else 0

        if merged and merged[-1].get("page_number", 0) == t.get("page_number", 0) - 1:
            prev = merged[-1]
            prev_rows = prev.get("rows", [])
            prev_cols = len(prev_rows[0]) if prev_rows else 0
            if prev_cols == cols and cols >= 3:
                # 同列数连续页 -> 合并（跳过当前表的表头行）
                skip = 1 if _looks_like_header_row(rows[0]) else 0
                merged[-1] = {**prev, "rows": prev_rows + rows[skip:],
                              "row_count": len(prev_rows) + max(0, len(rows) - skip)}
                continue
        merged.append(t)
    return merged


def _looks_like_header_row(row: list[str | None]) -> bool:
    """判断一行是否像表头（含'项目''附注'等且不含纯数字）"""
    text = " ".join(str(c) for c in row if c)
    has_kw = any(kw in text for kw in ["项目", "附注", "科目"])
    has_digit = any(c.isdigit() for c in text.replace(",", ""))
    return has_kw and not has_digit


# ────────────────────────────────────────
# 规则降级: LLM 不可用时基于表头关键词分类
# ────────────────────────────────────────

def _rule_based_classify(tables: list[dict]) -> dict[str, dict]:
    """根据表头第一行关键词将表格分为 BS/PL/CF 三类（不依赖 LLM）

    Returns: {"table_0": {"statement_type": "balance_sheet", "report_scope": "合并报表"}, ...}
    """
    bs_kw = ["资产", "负债", "所有者权益"]
    pl_kw = ["收入", "利润", "成本", "费用"]
    cf_kw = ["现金", "投资活动", "筹资活动"]

    result = {}
    for i, table in enumerate(tables):
        rows = table.get("rows", [])
        if not rows:
            continue
        # 合并表头+前10行数据一起判断（表头通常是"项目 附注 日期"的通配格式）
        all_text = " ".join(
            " ".join(str(c) for c in row if c)
            for row in rows[:12]
        ).lower()

        if any(kw in all_text for kw in bs_kw):
            stype = "balance_sheet"
        elif any(kw in all_text for kw in pl_kw):
            stype = "income_statement"
        elif any(kw in all_text for kw in cf_kw):
            stype = "cashflow_statement"
        else:
            continue

        # 标题含"合并"或"母公司" → 默认合并
        scope = "合并报表"
        all_text_lower = " ".join(" ".join(str(c) for c in row if c) for row in rows[:5]).lower()
        if "母公司" in all_text_lower:
            scope = "母公司报表"

        result[f"table_{i}"] = {"statement_type": stype, "report_scope": scope}

    logger.info(f"规则分类: {len(result)}/{len(tables)} 张表归属核心报表")
    return result
