"""A2层匹配算法单元测试"""

import pytest
from schemas.tags import HardTag, SoftTag, CompanyTags


class TestPeerMatching:
    """同行匹配测试"""

    def test_hard_tag_filter(self):
        """硬标签应精确锁定行业"""
        # TODO: 测试硬标签过滤逻辑
        pass

    def test_soft_tag_similarity(self):
        """软标签相似度计算"""
        # TODO: 测试软标签匹配评分
        pass
