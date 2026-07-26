"""按四大区块切分原始提取结果"""


class DocumentChunker:
    """根据章节标题规则，切分为四大区块"""

    SECTIONS = {
        "financial_data": ["财务报表", "资产负债表", "利润表", "现金流量表"],
        "management_discussion": ["管理层讨论与分析", "经营情况讨论与分析"],
        "footnotes": ["附注", "财务报表附注"],
        "company_overview": ["公司简介", "基本情况", "公司信息"],
    }

    def __init__(self, raw_data: dict):
        self.raw_data = raw_data

    def chunk(self) -> dict:
        """执行切分，返回四大区块"""
        # TODO: 实现基于章节标题的规则切分
        return {
            "financial_data": {"balance_sheet": [], "income_statement": [], "cashflow_statement": []},
            "management_discussion": {"sections": []},
            "footnotes": {"items": []},
            "company_overview": {"business_description": "", "industry_classification": ""},
            "metadata": self.raw_data.get("metadata", {}),
        }
