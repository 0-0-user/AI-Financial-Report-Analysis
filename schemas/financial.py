"""
==========================================================
 schemas/financial.py — B层输出：统一财务数据的数据结构
==========================================================

本文件定义了经过 B1 层提取和校验后的财务数据结构。
所有数值已经统一换算为"元"，字段名已映射为系统标准名称。

数据流向：
- FinancialStatement → B+层（逻辑异常检查）
- FinancialStatement → C层（MAD偏差计算）
- FinancialStatement → D层（推理分析）
- FinancialStatement → E层（评分和报告）

主要类型：
- FinancialField:    一个财务字段的数值（标准名 + 原始名 + 值 + 单位）
- ValidationCheck:   一条勾稽校验的结果（检查项名 + 是否通过 + 数值）
- ValidationResult:  勾稽校验汇总（全部是否通过 + 每项详情）
- FinancialStatement:完整财务数据（三大报表 + 校验结果）
"""

from pydantic import BaseModel
from typing import Optional


class FinancialField(BaseModel):
    """一个财务字段的数值（已统一换算为元）"""
    standard_name: str                           # 系统标准名（如 Revenue_Total）
    raw_name: str                                # 财报原始名（如 营业收入）
    value: float                                 # 换算为元后的数值
    original_unit: str                           # 原始单位（如 万元）
    report_type: str                             # 合并报表 / 母公司报表


class ValidationCheck(BaseModel):
    """一条勾稽校验的结果"""
    check_name: str                              # 检查项名称
    passed: bool                                 # 是否通过
    left_value: Optional[float] = None           # 等式左边值
    right_value: Optional[float] = None          # 等式右边值
    detail: str = ""                             # 详细说明


class ValidationResult(BaseModel):
    """勾稽校验汇总结果"""
    is_valid: bool                               # 是否全部通过
    checks: list[ValidationCheck]                # 每条检查的详情
    error_message: Optional[str] = None          # 阻断时的错误信息


class FinancialStatement(BaseModel):
    """一份完整的财务数据（所有数值已统一为元）"""
    company_name: str                            # 公司名称
    stock_code: str                              # 股票代码
    year: int                                    # 财报年份
    report_type: str                             # 合并报表 / 母公司报表
    balance_sheet: dict[str, FinancialField]     # 资产负债表（key=标准字段名）
    income_statement: dict[str, FinancialField]  # 利润表
    cashflow: dict[str, FinancialField]          # 现金流量表
    validation: ValidationResult                 # 勾稽校验结果
