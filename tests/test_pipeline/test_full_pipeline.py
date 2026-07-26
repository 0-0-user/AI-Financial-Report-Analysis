"""完整流水线集成测试"""

import pytest
from schemas.report import Report


class TestFullPipeline:
    """端到端流水线测试"""

    def test_pipeline_runs_with_mock(self):
        """mock 数据下流水线应完整执行"""
        # TODO: 组装完整 mock PipelineContext
        pass

    def test_pipeline_output_format(self):
        """输出格式应符合 Report schema"""
        pass

    def test_error_handling(self):
        """PDF 解析失败时应优雅降级"""
        pass
