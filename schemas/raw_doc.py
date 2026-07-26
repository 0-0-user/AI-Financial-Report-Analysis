from pydantic import BaseModel
from typing import Optional


class FinancialTable(BaseModel):
    """财务数据部分：三大报表的表格原始数据"""
    balance_sheet: list[dict]
    income_statement: list[dict]
    cashflow_statement: list[dict]


class ManagementDiscussion(BaseModel):
    """管理层讨论与分析部分"""
    sections: list[dict]  # [{title, content, page_number}]


class Footnotes(BaseModel):
    """附注明细部分"""
    items: list[dict]  # [{name, content, page_number}]


class CompanyOverview(BaseModel):
    """公司基本情况部分"""
    business_description: str
    industry_classification: str
    stock_code: Optional[str] = None
    company_name: Optional[str] = None


class RawDocument(BaseModel):
    """第0层完整输出"""
    metadata: dict
    financial_data: FinancialTable
    management_discussion: ManagementDiscussion
    footnotes: Footnotes
    company_overview: CompanyOverview
