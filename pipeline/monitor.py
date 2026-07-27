"""Pipeline 执行监控与审计日志

记录每次流水线运行的：
- 各层执行时间
- LLM 调用次数和 token 消耗
- 异常数量和严重度分布
- 最终得分

PDF 未提及，属于工程化补充。
"""

import time
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class LayerMetrics:
    """单层的执行指标"""
    name: str
    start_time: float = 0.0
    end_time: float = 0.0
    llm_calls: int = 0
    llm_tokens_input: int = 0
    llm_tokens_output: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def elapsed_ms(self) -> float:
        return (self.end_time - self.start_time) * 1000

    def to_dict(self) -> dict:
        return {
            "layer": self.name,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "llm_calls": self.llm_calls,
            "llm_tokens": self.llm_tokens_input + self.llm_tokens_output,
            "errors": len(self.errors),
        }


@dataclass
class PipelineMetrics:
    """完整流水线的执行指标"""
    pdf_file: str = ""
    stock_code: str = ""
    layers: list[LayerMetrics] = field(default_factory=list)
    total_llm_calls: int = 0
    total_llm_cost_usd: float = 0.0
    anomaly_count_bplus: int = 0
    anomaly_count_c: int = 0
    final_score: Optional[float] = None

    def start_layer(self, name: str) -> LayerMetrics:
        m = LayerMetrics(name=name, start_time=time.time())
        self.layers.append(m)
        return m

    def finish_layer(self, metrics: LayerMetrics):
        metrics.end_time = time.time()
        self.total_llm_calls += metrics.llm_calls
        logger.info(
            f"[{metrics.name}] 完成 ({metrics.elapsed_ms:.0f}ms, "
            f"LLM调用{metrics.llm_calls}次, 错误{len(metrics.errors)}个)"
        )

    def summary(self) -> str:
        """打印可读的汇总信息"""
        lines = [
            f"========== Pipeline 执行汇总 ==========",
            f"PDF: {self.pdf_file}",
            f"股票: {self.stock_code}",
            f"总分: {self.final_score or 'N/A'}",
            f"总 LLM 调用: {self.total_llm_calls} 次",
            f"估算 API 费用: ${self.total_llm_cost_usd:.4f}",
            f"B+ 异常: {self.anomaly_count_bplus} | C 偏离: {self.anomaly_count_c}",
            f"--- 各层耗时 ---",
        ]
        for layer in self.layers:
            lines.append(
                f"  {layer.name:25s} {layer.elapsed_ms:8.0f}ms  "
                f"LLM×{layer.llm_calls}"
            )
        total_ms = sum(l.elapsed_ms for l in self.layers)
        lines.append(f"  {'总计':25s} {total_ms:8.0f}ms")
        lines.append("=" * 45)
        return "\n".join(lines)
