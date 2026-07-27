"""
==========================================================
 schemas/enums.py — 公共枚举类型定义
==========================================================

本文件定义了系统中跨层使用的枚举常量，包括：
- ReportType：      报表类型（合并报表 / 母公司报表）
- AnomalySeverity： 异常来源类型（逻辑异常 / 偏差异常）
- ConfidenceTier：  置信度等级（高/中/低）

所有层统一引用这里的枚举值，避免用字符串硬编码。
"""

from enum import Enum


class ReportType(str, Enum):
    """报表类型"""
    CONSOLIDATED = "合并报表"
    PARENT = "母公司报表"


class AnomalySeverity(str, Enum):
    """异常来源类型"""
    LOGIC = "逻辑异常"       # B+ 层发现
    DEVIATION = "偏差异常"   # C 层发现


class ConfidenceTier(str, Enum):
    """置信度等级"""
    HIGH = "高置信度"
    MEDIUM = "中等置信度"
    LOW = "低置信度"
