"""
==========================================================
 schemas/tags.py — A层输出：行业标签的数据结构
==========================================================

A1 层大模型读完公司基本情况后，给公司打上定性标签。

标签分为两类：
- 硬标签：客观的行业分类（如同花顺三级行业"白酒"）
         用于 A2 层第一步——强制锁定同行范围
- 软标签：主观的商业模式特征（如"重资产"、"高毛利"、"To-C端"）
         用于 A2 层第二步——在同行业中找最相似的企业

数据流向：
- CompanyTags → A2层（匹配同行）
- CompanyTags → D层（路2推演假设的输入之一）
"""

from pydantic import BaseModel


class HardTag(BaseModel):
    """硬标签：行业分类"""
    system: str
    value: str


class SoftTag(BaseModel):
    """软标签：商业模式特征"""
    dimension: str
    value: str


class CompanyTags(BaseModel):
    """一家公司的完整标签"""
    company_name: str
    stock_code: str
    hard_tags: list[HardTag]
    soft_tags: list[SoftTag]
