"""A1层：语义提取与打标签——LLM 给公司打定性标签"""

from schemas.tags import CompanyTags


def run_tagging(raw_doc) -> CompanyTags:
    """大模型读取公司基本情况和行业背景，输出硬标签 + 软标签

    输入：raw_doc.company_overview
    流程：加载 prompt → 调用 LLM → 解析标签
    输出：CompanyTags（硬标签 + 软标签列表）
    """
    # TODO: 调用 LLM 进行标签分类
    return CompanyTags(
        company_name="",
        stock_code="",
        hard_tags=[],
        soft_tags=[],
    )
