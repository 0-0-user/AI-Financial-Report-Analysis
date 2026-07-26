"""A0层：外部宏观研报提取——LLM 联网搜索行业宏观事实"""


def run_macro_search(raw_doc) -> list[str]:
    """大模型联网搜索行业宏观情况，返回客观事实列表

    输入：原始文档中的公司基本信息和行业描述
    流程：生成搜索词 → 调用 LLM 联网搜索 → 提取客观事实 → 屏蔽主观评级
    输出：客观事实字符串列表
    """
    # TODO: 调用 llm/client.py 的 LLMClient
    # prompt = prompt_loader.load("a0_macro_search")
    # response = llm_client.chat(prompt, variables={...})
    # facts = response_parser.extract_facts(response)
    return []
