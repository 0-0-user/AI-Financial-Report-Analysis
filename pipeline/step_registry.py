"""步骤注册表——各层通过装饰器注册入口函数"""

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
