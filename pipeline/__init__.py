"""
==========================================================
 pipeline 包 — 全流程编排引擎
==========================================================

本包负责按顺序调度 0 -> A -> B -> B+ -> C -> D -> E 各层，
处理层间数据传递和错误传播。

使用方式: 
    from pipeline import Orchestrator

    orch = Orchestrator()
    report = orch.run("年报.pdf")

核心组件: 
- Orchestrator:  主调度器，依次执行各层
- PipelineContext: 数据背包，每一层的输出往里放，后一层从里取
- StepRegistry:   步骤注册表，各层通过装饰器注册入口
"""

from .orchestrator import Orchestrator
from .context import PipelineContext
from .step_registry import StepRegistry

__all__ = ["Orchestrator", "PipelineContext", "StepRegistry"]
