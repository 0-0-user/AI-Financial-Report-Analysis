"""
==========================================================
 pipeline/step_registry.py — 步骤注册表
==========================================================

各层通过 @registry.register("layer_xxx") 装饰器注册自己的入口函数。
Orchestrator 不需要直接 import 各层，而是通过 registry 查找。

这种设计的好处：
- 解耦：Orchestrator 不关心各层的具体实现
- 可插拔：可以替换某一层的实现而不影响其他层
- 可跳过：Orchestrator 通过 registry 知道所有已注册的步骤

使用方式：
    @registry.register("layer_b")
    def run(ctx):
        # 实现代码
        pass
"""

from typing import Callable


class StepRegistry:
    """将各层的入口函数注册到统一映射表"""

    def __init__(self):
        self._steps: dict[str, Callable] = {}

    def register(self, name: str) -> Callable:
        """装饰器：注册一个步骤"""
        def decorator(func: Callable) -> Callable:
            self._steps[name] = func
            return func
        return decorator

    def get(self, name: str) -> Callable:
        if name not in self._steps:
            raise KeyError(f"步骤 '{name}' 未注册")
        return self._steps[name]

    def list_steps(self) -> list[str]:
        return list(self._steps.keys())


# 全局单例
registry = StepRegistry()
