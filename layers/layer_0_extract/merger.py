"""处理跨页表格拼接和去重"""


class TableMerger:
    """合并跨页断裂的表格"""

    def __init__(self, financial_data: dict):
        self.financial_data = financial_data

    def merge_cross_page_tables(self) -> dict:
        """检测并合并跨页表格"""
        # TODO: 实现跨页表格拼接逻辑
        return self.financial_data

    def deduplicate(self) -> dict:
        """去除重复提取的行"""
        return self.financial_data
