"""各层功能实现 — import 本包即触发所有 @registry.register"""

# 显式导入各层，触发 @registry.register 装饰器完成步骤注册
import layers.layer_0_extract  # noqa: F401
import layers.layer_a_benchmark  # noqa: F401
import layers.layer_amacro  # noqa: F401
import layers.layer_b_extract  # noqa: F401
import layers.layer_bplus_internal  # noqa: F401
import layers.layer_c_deviation  # noqa: F401
import layers.layer_d_reasoning  # noqa: F401
import layers.layer_e_output  # noqa: F401
