"""
==========================================================
 schemas/b0_guide.py — B0层输出：表头语义指引的数据结构
==========================================================

B0 层大模型读取报表表头后，输出这份结构化指引。
B1 层代码（纯 Pandas）根据这份指引去定位表格、取数、换算单位。

核心逻辑：LLM 只读表头文字，不碰数值；
          代码只按坐标取数，不做语义理解。

主要类型：
- FieldMapping:  单个字段的映射（原始名 → 标准名 + 表格坐标 + 单位）
- ColumnHeader:  表格列头描述（原始文字 → 标准列名）
- TableGuide:    一张报表的完整指引（表名、类型、单位、所有字段映射）
- B0Guide:       B0层完整输出，包含所有报表的指引
"""

from pydantic import BaseModel
from typing import Optional


class FieldMapping(BaseModel):
    """单个字段的映射指引"""
    raw_name: str                                # 财报原始字段名（如"营业收入"）
    standard_name: str                           # 系统标准字段名（如"Revenue_Total"）
    row_index: int                               # 在表格中的行号（从0开始）
    col_index: int                               # 在表格中的列号（从0开始）
    unit: str = "元"                             # 该字段的单位
    is_negative: bool = False                    # 是否为负值（如支出类科目）


class ColumnHeader(BaseModel):
    """表格列头描述"""
    index: int                                   # 列号
    raw_text: str                                # 列头原文（如"期末余额"）
    standard_name: str                           # 标准列名（如"end_balance"）


class TableGuide(BaseModel):
    """一张报表的指引"""
    table_name: str                              # 报表名称
    report_type: str = "合并报表"                 # 合并报表 / 母公司报表
    overall_unit: str = "元"                     # 报表整体单位
    columns: list[ColumnHeader]                  # 各列描述
    field_mappings: list[FieldMapping]           # 字段映射列表


class B0Guide(BaseModel):
    """B0层完整输出：指引B1层提取数据的蓝图"""
    tables: list[TableGuide]                     # 各报表的指引
    raw_document_name: Optional[str] = None      # 来源文件名
