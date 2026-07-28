"""
==========================================================
 schemas/tags.py — A层输出：硬标签 + 财务数字画像
==========================================================

A1 层输出两类信息：
1. 硬标签（HardTag）：同花顺三级行业分类（客观事实）
2. 财务数字画像（FinancialProfile）：6 维特征等级（LLM 从业务描述推断）

数据流向：
- CompanyTags → A2层（使用 financial_profile 做向量匹配）
- CompanyTags → D层（使用 financial_profile + hard_tags 做假设推演）
"""

from pydantic import BaseModel


# ────────────────────────────────────────────
# 等级→数值映射常量（供 A2 向量匹配使用）
# ────────────────────────────────────────────
LEVEL_TO_SCORE: dict[str, float] = {
    "极高": 1.0,
    "高": 0.8,
    "中高": 0.8,
    "中等": 0.5,
    "中": 0.5,
    "低": 0.2,
}

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
    """财务数字画像：6 维特征等级数组

    顺序固定为 FINANCIAL_DIMENSIONS：
    [毛利率, 净利率, 周转率, 负债率, 研发率, 销售率]

    示例：["极高", "极高", "低", "低", "低", "高"]
    """
    levels: list[str]  # 长度恒为 6

    @property
    def as_numeric(self) -> list[float]:
        """等级→数值映射，用于余弦相似度匹配"""
        return [LEVEL_TO_SCORE.get(lv, 0.0) for lv in self.levels]


class CompanyTags(BaseModel):
    """一家公司的硬标签 + 财务数字画像"""
    company_name: str
    stock_code: str
    hard_tags: list[HardTag]
    financial_profile: FinancialProfile | None = None
