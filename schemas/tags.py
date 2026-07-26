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
