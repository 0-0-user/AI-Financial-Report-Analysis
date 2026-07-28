"""第0层：表格合并器 — 处理跨页表格拼接和去重

常见问题：
1. 资产负债表跨 2~3 页，每页表头重复 + 数据延续
2. 跨页后第一行列名与上一页末行重复
3. OCR 重影导致同一行被识别两次

策略：
- 拼接（merge）：检测连续两页表格表头一致 → 去掉第二页表头，拼接数据行
- 去重（deduplicate）：检测完全相同的行 → 只保留第一条
- 清洗（clean）：移除空行、单位行、表头重复行
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class TableMerger:
    """合并跨页表格，去重并清洗"""

    # 单位行常见模式
    UNIT_PATTERNS = re.compile(
        r"^(单位[：:]|单位\s|(?:人民币)?[元千百万元亿元]+$|in\s+(?:CNY|RMB))",
        re.IGNORECASE,
    )

    def __init__(self, financial_data: dict):
        """
        Args:
            financial_data: chunker 输出的 financial_data
                {"balance_sheet": [RawTableRow], "income_statement": [...], ...}
        """
        self.financial_data = financial_data

    def merge_cross_page_tables(self) -> dict:
        """检测并合并跨页表格

        将同一报表类型（如所有资产负债表页）的表格拼成一张完整表格。
        """
        result = {}
        for sheet_name in ["balance_sheet", "income_statement", "cashflow_statement"]:
            rows = self.financial_data.get(sheet_name, [])
            if not rows:
                result[sheet_name] = []
                continue

            merged = self._merge_rows(rows)
            result[sheet_name] = merged

        return result

    def deduplicate(self) -> dict:
        """去除重复行

        比较策略：同一页码 + 相同列值 → 视为重复，只保留第一条。
        """
        result = {}
        for sheet_name in ["balance_sheet", "income_statement", "cashflow_statement"]:
            rows = self.financial_data.get(sheet_name, [])
            seen = set()
            unique = []
            for row in rows:
                # 用 (page_number, 第一列值, 第二列值, 行内列数) 作为去重键
                cols = row.get("columns", {})
                key = (
                    row.get("page_number", 0),
                    cols.get("col_0", "").strip(),
                    cols.get("col_1", "").strip(),
                    len(cols),
                )
                if key not in seen:
                    seen.add(key)
                    unique.append(row)
                else:
                    logger.debug(f"去重: {sheet_name} 行{row.get('row_index')} 重复")

            removed = len(rows) - len(unique)
            if removed:
                logger.info(f"{sheet_name} 去重: 移除 {removed} 行重复")

            result[sheet_name] = unique
        return result

    def clean(self) -> dict:
        """清洗：移除空行、单位行、表头重复行"""
        result = {}
        for sheet_name in ["balance_sheet", "income_statement", "cashflow_statement"]:
            rows = self.financial_data.get(sheet_name, [])
            cleaned = []
            header_keywords = set()  # 记录已见过的表头

            for row in rows:
                cols = row.get("columns", {})
                row_text = " ".join(str(v) for v in cols.values()).strip()

                # 空行跳过
                if not row_text:
                    continue

                # 单位行跳过
                if self.UNIT_PATTERNS.search(row_text):
                    continue

                # 重复表头跳过
                row_key = row_text[:30]  # 前 30 个字符作为表头指纹
                if len(cleaned) > 0 and row_key in header_keywords:
                    continue
                if self._looks_like_header(row_text):
                    header_keywords.add(row_key)

                cleaned.append(row)

            removed = len(rows) - len(cleaned)
            if removed:
                logger.info(f"{sheet_name} 清洗: 移除 {removed} 行（空行/单位行/重复表头）")

            result[sheet_name] = cleaned
        return result

    # ────────────────────────────────────────
    # 内部方法
    # ────────────────────────────────────────

    def _merge_rows(self, rows: list[dict]) -> list[dict]:
        """合并同一报表类型跨页的行

        跨页判断：当两页的第一行（数据首行）列数相同且表头关键词匹配时，
        认为它们是同一张表的延续 → 合并。
        """
        if len(rows) < 3:
            return rows

        # 按页码分组
        page_groups: dict[int, list[dict]] = {}
        for row in rows:
            page = row.get("page_number", 0)
            page_groups.setdefault(page, []).append(row)

        pages = sorted(page_groups.keys())
        if len(pages) <= 1:
            return rows

        merged = []
        for i, page_num in enumerate(pages):
            page_rows = page_groups[page_num]

            if i == 0:
                merged.extend(page_rows)
                continue

            # 检查是否与前一页连续
            prev_page = pages[i - 1]
            if self._is_continuation(page_groups[prev_page], page_rows):
                # 连续表：去掉本页的表头行 + 单位行
                start_idx = self._find_data_start(page_rows)
                merged.extend(page_rows[start_idx:])
            else:
                merged.extend(page_rows)

        return merged

    def _is_continuation(self, prev_rows: list[dict], curr_rows: list[dict]) -> bool:
        """判断两页的表格是否为同一张表的延续

        判断依据：
        1. 两页的表头前几个关键字相同
        2. 前一页末行和当前页首行的列数相近
        """
        if not prev_rows or not curr_rows:
            return False

        # 提取两页的前几行做表头指纹
        prev_header = self._row_text(prev_rows[0])[:20]
        curr_header = self._row_text(curr_rows[0])[:20]

        # 前一页最后一行的列数 vs 当前页第一行
        prev_cols = len(prev_rows[-1].get("columns", {}))
        curr_cols = len(curr_rows[0].get("columns", {}))

        same_header = prev_header == curr_header
        similar_cols = abs(prev_cols - curr_cols) <= 2

        return same_header and similar_cols

    def _find_data_start(self, rows: list[dict]) -> int:
        """找到数据起始行（跳过表头/单位）"""
        for i, row in enumerate(rows):
            text = self._row_text(row)
            if not self._looks_like_header(text) and not self.UNIT_PATTERNS.search(text):
                return i
        return 0

    @staticmethod
    def _row_text(row: dict) -> str:
        """把行的各列值拼成字符串"""
        cols = row.get("columns", {})
        return " ".join(str(v) for v in cols.values()).strip()

    @staticmethod
    def _looks_like_header(text: str) -> bool:
        """判断文本是否更像表头而非数据行"""
        # 表头特征：短、含"项目/科目/指标/附注"等词、不含数字
        if len(text) > 60:
            return False
        header_kw = ["项目", "科目", "指标", "附注", "报表项目", "行次"]
        has_kw = any(kw in text for kw in header_kw)
        has_digit = any(c.isdigit() for c in text)
        return has_kw and not has_digit
