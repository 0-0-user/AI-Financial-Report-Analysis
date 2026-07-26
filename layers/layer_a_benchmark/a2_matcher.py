"""A2层：匹配模式相似的企业——纯代码匹配+算基准"""

from schemas.tags import CompanyTags
from schemas.benchmark import Benchmark, PeerCompany


def run_matching(tags: CompanyTags) -> Benchmark:
    """匹配相似企业并计算基准

    流程：
    1. 硬标签锁定行业范围
    2. 软标签过滤找 3~5 家最相似同行
    3. 计算同行中位数（横向基准）
    4. 提取该企业自身 5 年数据均值（纵向基准）

    输入：CompanyTags
    输出：Benchmark（同行中位数 + 历史均值 + 同行列表）
    """
    # TODO: 从 data/benchmarks/ 读取数据
    # TODO: 实现软标签相似度匹配
    # TODO: 计算中位数和均值
    return Benchmark(
        peer_median={},
        historical_mean={},
        peer_companies=[],
    )
