"""D1路1：从年报附注和管理层讨论中查找异常的解释"""

from typing import Any
from schemas.raw_doc import RawDocument
from schemas.reasoning import Explanation


def lookup_in_annual_report(raw_doc: RawDocument, anomaly: Any) -> list[Explanation]:
    """大模型读取年报原文，查找与异常指标相关的解释

    输入：年报的【附注】和【管理层讨论】部分 + 异常信息
    流程：搜索相关内容 → LLM 提取关键信息 → 标记是否含糊
    输出：Explanation 列表
    """
    # TODO: 调用 LLM 进行分析
    # 从 raw_doc 中查找与异常指标相关的附注和管理层讨论段落
    return []


def _find_relevant_sections(raw_doc: RawDocument, keyword: str) -> list[dict]:
    """根据关键词在年报原文中定位相关段落"""
    sections = []
    for item in raw_doc.footnotes.items:
        if keyword in item.get("name", "") or keyword in item.get("content", ""):
            sections.append(item)
    for section in raw_doc.management_discussion.sections:
        if keyword in section.get("title", "") or keyword in section.get("content", ""):
            sections.append(section)
    return sections
