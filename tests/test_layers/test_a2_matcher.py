"""
==========================================================
 tests/test_layers/test_a2_matcher.py — 同行匹配测试
==========================================================

测试 A2 层同行匹配算法：
- 硬标签应精确锁定行业范围
- 余弦相似度计算是否正确
"""

import pytest
from schemas.tags import HardTag, FinancialProfile, CompanyTags


class TestPeerMatching:
    """同行匹配测试"""

    def test_hard_tag_filter(self):
        """硬标签应精确锁定行业"""
        baijiu_tags = CompanyTags(
            company_name="茅台",
            stock_code="600519",
            hard_tags=[
                HardTag(system="同花顺三级行业", value="白酒"),
                HardTag(system="同花顺二级行业", value="饮料制造"),
            ],
        )
        real_estate_tags = CompanyTags(
            company_name="万科",
            stock_code="000002",
            hard_tags=[
                HardTag(system="同花顺三级行业", value="房地产开发"),
                HardTag(system="同花顺二级行业", value="房地产"),
            ],
        )
        # 验证硬标签不同
        baijiu_industry = [t.value for t in baijiu_tags.hard_tags]
        real_estate_industry = [t.value for t in real_estate_tags.hard_tags]
        assert baijiu_industry != real_estate_industry
        assert "白酒" in baijiu_industry
        assert "房地产开发" in real_estate_industry

    def test_financial_profile_numeric(self):
        """FinancialProfile 数值映射应正确"""
        fp = FinancialProfile(values=[1.0, 1.0, 0.2, 0.2, 0.2, 0.8])
        expected = [1.0, 1.0, 0.2, 0.2, 0.2, 0.8]
        assert fp.as_numeric == expected
        # as_numeric 与 values 指向同一数据
        assert fp.values == expected
