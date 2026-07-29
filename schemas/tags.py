"""
==========================================================
 schemas/tags.py — A层输出：硬标签 + 财务数字画像
==========================================================

A1 层输出两类信息：
1. 硬标签（HardTag）：同花顺三级行业分类（客观事实）
2. 财务数字画像（FinancialProfile）：6 维特征连续值（从 akshare 计算）

数据流向：
- CompanyTags → A2层（使用 financial_profile.values 做向量匹配）
- CompanyTags → D层（使用 financial_profile + hard_tags 做假设推演）

FinancialProfile 存储的是经稳健标准化后的连续值，直接用于余弦相似度匹配。
"""

from pydantic import BaseModel


# 6 维度顺序（全局统一）
FINANCIAL_DIMENSIONS: list[str] = [
    "毛利率水平",
    "净利率水平",
    "总资产周转率",
    "资产负债率",
    "研发费用率",
    "销售费用率",
]


class HardTag(BaseModel):
    """硬标签：同花顺三级行业分类"""
    system: str = "同花顺三级行业"
    value: str


class FinancialProfile(BaseModel):
    """财务数字画像：6 维特征连续值（稳健标准化后）

    顺序固定为 FINANCIAL_DIMENSIONS：
    [毛利率, 净利率, 周转率, 负债率, 研发率, 销售率]

    存储的是经同行池稳健标准化后的连续值，
    直接用于余弦相似度匹配，不再经过离散等级中转。

    示例：[0.82, 0.35, -0.12, -0.45, 0.60, 1.20]
    """
    values: list[float]  # 长度恒为 6，稳健标准化后的连续值

    @property
    def as_numeric(self) -> list[float]:
        """别名，直接返回 values（保持向后兼容）"""
        return self.values


class CompanyTags(BaseModel):
    """一家公司的硬标签 + 财务数字画像"""
    company_name: str
    stock_code: str
    hard_tags: list[HardTag]
    financial_profile: FinancialProfile | None = None
