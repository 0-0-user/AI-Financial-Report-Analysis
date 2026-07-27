# AI-Financial-Report-Analysis

AI 驱动的上市公司年报智能分析系统。

利用大模型 + 代码严格分工，自动分析一份几百页的年报 PDF，输出**置信度打分**和**结构化分析报告**。

---

## 核心设计原则

```
大模型做的事                代码做的事
─────────────────────       ─────────────────────
理解文字（年报原文）         提取数字、换算单位
标签分类（行业定性）         勾稽校验（会计恒等式）
逻辑推演（商业常识）         MAD 偏差计算
检索研报（宏观背景）         概率扣分、置信度打分

大模型不做任何数学计算       代码不做任何语义理解
```

**为什么这样分？**
- 大模型做数学会算错（数值幻觉）
- 代码做语义会误判（规则写不完）
- 各司其职，隔离风险

---

## 六层架构

整条流水线按顺序执行 7 个步骤，每层只做一件事，输出传给下一层：

| 层 | 名字 | 做什么 | 谁做 |
|----|------|--------|------|
| 第0层 | PDF 提取 | 把年报 PDF 拆成结构化 JSON | 工具（MinerU） |
| A层 | 行业定性 | 打行业标签 → 找同行 → 算基准 | LLM + 代码 |
| B层 | 财务数据提取 | 识别表头 → 提取数字 → 校验恒等式 | LLM + 代码 |
| B+层 | 逻辑检查 | 净现比 / 存贷双高 / 母子资金分离 | **只代码** |
| C层 | 偏差计算 | MAD 算法算指标偏离同行多远 | **只代码** |
| D层 | 原因推理 | 双路径：查原文解释 + 推演假设 → 分配概率 | LLM |
| E层 | 输出报告 | 置信度打分 + 结构化报告 | **代码** + LLM |

**阻断机制**：B1 层勾稽校验（资产=负债+权益）失败 → 跳过后续所有层 → 直接输出系统异常报告。

---

## 快速开始

### 1. 安装

```bash
# 克隆仓库
git clone https://github.com/0-0-user/AI-Financial-Report-Analysis.git
cd AI-Financial-Report-Analysis

# 安装依赖（推荐在虚拟环境中）
pip install -e ".[dev,pdf]"
```

### 2. 配置 API Key

复制环境变量模板并填入 Key：

```bash
cp .env.example .env
```

编辑 `.env`，填入你的 API Key：

```
ANTHROPIC_API_KEY=sk-ant-xxxx     # 主力 LLM（Anthropic Claude）
OPENAI_API_KEY=sk-xxxx             # 备用 LLM（OpenAI GPT）
```

### 3. 准备数据

```bash
# 创建数据目录
mkdir -p data/raw data/processed data/benchmarks data/outputs

# 把年报 PDF 放入 data/raw/
# 例如：data/raw/600519_2024.pdf
```

### 4. 验证 PDF 可解析性

```bash
python scripts/validate_pdf.py data/raw/600519_2024.pdf
```

### 5. 运行分析

```bash
# 完整分析
python scripts/run_pipeline.py --pdf data/raw/600519_2024.pdf

# 只跑某一层开始（跳过前面步骤，调试用）
python scripts/run_pipeline.py --pdf xxx.pdf --from layer_c

# 跳过 LLM 调用（用已有缓存数据）
python scripts/run_pipeline.py --pdf xxx.pdf --no-llm
```

### 6. 查看报告

```bash
python scripts/show_report.py data/outputs/600519_2024_report.json
```

---

## 配置说明

所有配置都在 `config/` 目录下，非开发人员也能修改：

| 文件 | 内容 | 谁改 |
|------|------|------|
| `config/weights.yaml` | E1 层打分权重（每个指标的 base_weight / direction） | 分析师 |
| `config/thresholds.yaml` | B+/C 层异常判定阈值 | 分析师 |
| `config/industry_tags.yaml` | A 层行业标签体系 | 分析师 |
| `config/financial_fields.yaml` | B 层统一财务字段映射表 | 开发 |
| `config/prompts/\*.yaml` | 所有 LLM prompt 模板 | 分析师/开发 |

**修改 prompt 不需要改代码**，直接编辑 `config/prompts/` 下的 YAML 文件即可。

---

## 项目结构

```
AI-Financial-Report-Analysis/
├── config/                  # 全局配置 + prompt 模板
│   ├── prompts/             # 所有 LLM prompt（YAML + Jinja2）
│   ├── weights.yaml         # E1 层打分权重
│   └── thresholds.yaml      # 异常判定阈值
├── schemas/                 # 数据契约（Pydantic v2）
│   ├── raw_doc.py           # 第0层：PDF 提取结果
│   ├── financial.py         # B层：统一财务数据
│   ├── anomaly.py           # B+/C层：异常清单
│   ├── reasoning.py         # D层：推理与概率分配
│   └── report.py            # E层：最终报告
├── layers/                  # 六层实现
│   ├── layer_0_extract/     # 第0层：PDF 解析
│   ├── layer_a_benchmark/   # A层：行业基准
│   ├── layer_b_extract/     # B层：财务数据提取
│   ├── layer_bplus_internal/# B+层：逻辑检查
│   ├── layer_c_deviation/   # C层：MAD 偏差计算
│   ├── layer_d_reasoning/   # D层：双路径推理
│   └── layer_e_output/      # E层：评分+报告
├── pipeline/                # 流水线编排
│   ├── orchestrator.py      # 主调度器
│   ├── step_registry.py     # 步骤注册中心
│   └── context.py           # 层间数据上下文
├── llm/                     # LLM 调用网关
│   ├── client.py            # 统一调用接口（Anthropic / OpenAI）
│   ├── prompt_loader.py     # Prompt 模板加载
│   └── response_parser.py   # 输出解析
├── scripts/                 # 命令行工具
│   ├── run_pipeline.py      # 运行流水线
│   ├── validate_pdf.py      # 验证 PDF
│   ├── show_report.py       # 查看报告
│   ├── batch_analyze.py     # 批量分析
│   └── build_benchmark_db.py# 构建基准库
├── tests/                   # 测试
│   ├── test_layers/         # 各层单元测试
│   ├── test_llm/            # LLM 网关测试
│   └── test_pipeline/       # 流水线集成测试
└── doc/                     # 架构文档
```

---

## 开发指南

### 运行测试

```bash
# 所有测试
pytest -v

# 只看某个文件
pytest tests/test_layers/test_c_mad.py -v

# 带覆盖率
pytest --cov=. tests/
```

### 代码规范

```bash
ruff check .       # 代码检查
ruff format .      # 自动格式化
```

### 流水线调试

```bash
# 跳过某几层，快速验证特定层逻辑
python scripts/run_pipeline.py --pdf xxx.pdf --from layer_e --no-llm
```

---

## 技术栈

| 组件 | 技术 |
|------|------|
| 语言 | Python 3.11+ |
| 数据校验 | Pydantic v2 |
| 数据分析 | Pandas / NumPy |
| LLM SDK | Anthropic SDK / OpenAI SDK |
| Prompt 引擎 | Jinja2（YAML 模板） |
| PDF 解析 | pdfplumber / MinerU |
| 测试 | pytest |
| 代码规范 | ruff |

---

## 分支策略

- `master` — 对外发布版本，只含核心文件（README / config / doc）
- `dev` — 内部开发版本，包含所有实现代码
