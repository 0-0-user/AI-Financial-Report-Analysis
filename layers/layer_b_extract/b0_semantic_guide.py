"""B0层：表头语义指引——LLM 识别表头文字、字段映射和单位"""


def run_semantic_guide(raw_doc) -> dict:
    """大模型只读表头文字，不接触数值数据

    输入：raw_doc.financial_data（仅表头）
    流程：提取表头 → LLM 识别字段 → 输出映射指引
    输出指引包含：
    - table_name: 报表名称
    - report_type: 合并/母公司
    - unit: 整体单位
    - field_mapping: 字段名映射和位置
    """
    # TODO: 调用 LLM 进行表头识别
    return {
        "table_name": "",
        "report_type": "",
        "unit": "",
        "field_mapping": [],
    }
