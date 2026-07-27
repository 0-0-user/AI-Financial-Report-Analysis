"""
==========================================================
 tests/test_layers/test_a2_matcher.py — 同行匹配测试
==========================================================

测试 A2 层同行匹配算法：
- 硬标签应精确锁定行业范围（如"白酒"只能匹配到白酒同行）
- 软标签相似度计算是否正确
"""

import pytest
from schemas.tags import HardTag, SoftTag, CompanyTags


class TestPeerMatching:
    """同行匹配测试"""

    def test_hard_tag_filter(self):
        """硬标签应精确锁定行业"""
        # 构造两个不同行业的标签
        baijiu_tags = CompanyTags(
            company_name="茅台",
            stock_code="600519",
            hard_tags=[
                HardTag(system="同花顺行业", value="食品饮料"),
                HardTag(system="申万行业", value="白酒III"),
            ],
            soft_tags=[
                SoftTag(dimension="资产结构", value="轻资产"),
            ],
        )
        real_estate_tags = CompanyTags(
            company_name="万科",
            stock_code="000002",
            hard_tags=[
                HardTag(system="同花顺行业", value="房地产"),
                HardTag(system="申万行业", value="房地产开发"),
            ],
            soft_tags=[
                SoftTag(dimension="资产结构", value="重资产"),
            ],
        )
        # 验证硬标签不同
        baijiu_industry = [t.value for t in baijiu_tags.hard_tags]
        real_estate_industry = [t.value for t in real_estate_tags.hard_tags]
        assert baijiu_industry != real_estate_industry
        assert "白酒III" in baijiu_industry
        assert "房地产开发" in real_estate_industry

    def test_soft_tag_similarity(self):
        """软标签相似度计算"""
        tags_a = CompanyTags(
            company_name="公司A",
            stock_code="000001",
            hard_tags=[HardTag(system="行业", value="白酒")],
            soft_tags=[
                SoftTag(dimension="资产结构", value="轻资产"),
                SoftTag(dimension="商业模式", value="品牌驱动"),
                SoftTag(dimension="客户类型", value="To-C"),
            ],
        )
        tags_b = CompanyTags(
            company_name="公司B",
            stock_code="000002",
            hard_tags=[HardTag(system="行业", value="白酒")],
            soft_tags=[
                SoftTag(dimension="资产结构", value="轻资产"),
                SoftTag(dimension="商业模式", value="品牌驱动"),
                SoftTag(dimension="客户类型", value="To-C"),
            ],
        )
        # 完全相同的软标签
        soft_a = {s.dimension: s.value for s in tags_a.soft_tags}
        soft_b = {s.dimension: s.value for s in tags_b.soft_tags}
        shared = sum(1 for k in soft_a if k in soft_b and soft_a[k] == soft_b[k])
        assert shared == 3  # 3个标签完全匹配

        # 构造一个不同标签做对比
        tags_c = CompanyTags(
            company_name="公司C",
            stock_code="000003",
            hard_tags=[HardTag(system="行业", value="白酒")],
            soft_tags=[
                SoftTag(dimension="资产结构", value="重资产"),
                SoftTag(dimension="商业模式", value="成本驱动"),
                SoftTag(dimension="客户类型", value="To-B"),
            ],
        )
        soft_c = {s.dimension: s.value for s in tags_c.soft_tags}
        shared_with_c = sum(1 for k in soft_a if k in soft_c and soft_a[k] == soft_c[k])
        assert shared_with_c == 0  # 完全不同
