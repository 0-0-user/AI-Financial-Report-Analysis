"""
==========================================================
 schemas/raw_doc.py — 第0层: PDF提取结果的数据结构
==========================================================

本文件定义了 PDF 解析工具将年报拆解后的标准输出格式。
一份年报 PDF 经过第0层 (pdfplumber) 处理后，输出一个 RawDocument 对象，
包含四大区块 (财务数据、管理层讨论、附注、公司基本情况) 。

数据流向: 
- RawDocument.financial_data       -> B 层 (提取具体数值) 
- RawDocument.management_discussion -> D 层 (查找异常解释) 
- RawDocument.footnotes            -> D 层 (查找附注解释) 
- RawDocument.company_overview     -> A 层 (打标签) 

主要类型: 
- RawTableRow:        OCR识别出的一行表格数据
- FinancialTable:     三大报表的原始OCR数据
- DiscussionSection:  管理层讨论的一个段落
- FootnoteItem:       一条附注
- CompanyOverview:    公司基本情况 (名称、代码、业务、行业) 
- DocumentMetadata:   PDF元信息 (文件名、页数、解析工具) 
- RawDocument:        完整的解析输出 (四大区块 + 元信息) 
"""

from pydantic import BaseModel
from typing import Optional


class RawTableRow(BaseModel):
    """OCR识别出的一行表格数据"""
    row_index: int                               # 行号
    columns: dict[str, str]                      # {列名: 单元格文本}
    page_number: int                             # 所在页码


class FinancialTable(BaseModel):
    """财务数据部分: 三大报表的原始OCR数据

    合并报表进 balance_sheet/income_statement/cashflow_statement（B0/B1 主用，供 B/C/D/E 分析）；
    母公司报表单独存 parent_* 桶（供 B+ 层母子资金分离度检查），两者彻底分离不混行。
    """
    balance_sheet: list[RawTableRow]             # 合并资产负债表
    income_statement: list[RawTableRow]          # 合并利润表
    cashflow_statement: list[RawTableRow]        # 合并现金流量表
    parent_balance_sheet: list[RawTableRow] = []       # 母公司资产负债表
    parent_income_statement: list[RawTableRow] = []    # 母公司利润表
    parent_cashflow_statement: list[RawTableRow] = []  # 母公司现金流量表


class DiscussionSection(BaseModel):
    """管理层讨论与分析的一个段落"""
    title: str                                   # 小节标题
    content: str                                 # 正文内容
    page_number: int                             # 起始页码


class ManagementDiscussion(BaseModel):
    """管理层讨论与分析部分"""
    sections: list[DiscussionSection]


class FootnoteItem(BaseModel):
    """一条附注"""
    name: str                                    # 附注名称 (如"存货") 
    content: str                                 # 附注正文
    page_number: int                             # 页码
    is_table: bool = False                       # 是否为表格格式


class Footnotes(BaseModel):
    """附注明细部分"""
    items: list[FootnoteItem]


class CompanyOverview(BaseModel):
    """公司基本情况部分"""
    company_name: Optional[str] = None           # 公司全称
    stock_code: Optional[str] = None             # 股票代码
    business_description: str                    # 主营业务描述
    industry_classification: str                 # 财报原文中的行业分类


class DocumentMetadata(BaseModel):
    """PDF 元信息"""
    file_name: str                               # 文件名
    page_count: int                              # 总页数
    report_year: Optional[int] = None            # 年报年份
    extract_tool: str = "pdfplumber"                 # 解析工具
    extract_date: Optional[str] = None           # 解析日期


class RawDocument(BaseModel):
    """第0层完整输出: 一份PDF解析后的全部内容"""
    metadata: DocumentMetadata
    financial_data: FinancialTable               # -> 流向 B 层
    management_discussion: ManagementDiscussion   # -> 流向 D 层
    footnotes: Footnotes                         # -> 流向 D 层
    company_overview: CompanyOverview             # -> 流向 A 层
