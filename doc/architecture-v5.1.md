# AI+金融财报分析系统完整全栈框架 v5.1

## 一、核心思路与原则
利用大模型的能力对上市公司年报进行解读，从财务报表、行业性质等角度分析该企业年度经营情况、发展前景和投资价值。
整个系统涉及大模型、代码、数学计算，整体原则如下：
1. **LLM 只做语义理解**：理解文字、打标签、逻辑推演、检索信息。**LLM 不参与任何数学计算。**
2. **代码只做数学运算**：提取数字、换算单位、勾稽校验、MAD 偏差计算、余弦相似度匹配、概率分配评分。**代码不进行任何语义理解。**
3. **硬标签查表优先**：客观行业分类（同花顺三级/二级/一级行业）从 CSV 数据源查表确定，纯代码降级，不依赖 LLM。
4. **同行匹配用连续值向量**：A2 层不再依赖离散等级做余弦相似度，改用稳健标准化后的连续值向量，消除阈值映射信息损失。
5. **知识来源严格隔离**：D1 路1（查原文）和路2（外部推演）的知识来源完全正交，互不越界。LLM 失败不做降级，全流程中断。
6. **整体实现流程**：先看行业定性并画数字画像 → 再用向量匹配找同行基准 → 提取财务数据并校验 → 发现异常偏差 → 结合财报原文和外部宏观情报找原因 → 给出概率化的综合判断。

---

## 二、系统六层架构

### 第0层：PDF数据提取
| 项目 | 说明 |
|------|------|
| **任务** | 将年报 PDF 解析为结构化 JSON |
| **实现** | pdfplumber 提取 text+tables；chunker 规则切分四大区块；文本纠错修复 OCR 错误；merger 跨页表格合并去重 |
| **输出** | RawDocument（财务数据 / 管理层讨论 / 附注 / 公司概况） |

### A层：找对比基准（行业定性 + 数字画像）

#### A0：外部宏观情报提取
- 两步流程：LLM 生成 4 维度搜索词 → SerpAPI 搜索 → LLM 提取结构化宏观事实（客观数据+来源+日期）
- v5.1 清理：移除冗余函数 `_extract_materials` / `_extract_downstream` 及对应 prompt 变量

#### A1：纯代码查硬标签
- CSV 查表（同花顺三级/二级/一级行业）：股票代码精确匹配 → 公司名模糊匹配 → 关键词降级
- 纯代码，无 LLM

#### A2：数字画像匹配同行（纯代码向量运算）
| 项 | v5.0 | v5.1 |
|----|------|------|
| **标准化** | 离散等级（极高/高/中等/低） | 连续值 `(x-median)/IQR` |
| **合并** | 等级取中位数 | 连续值逐维中位数 |
| **匹配** | 余弦相似度（等级→数值） | 余弦相似度（连续值直接算） |
| **行业池** | 同花顺三级行业（257 个） | 同花顺**二级行业**（更宽，样本更足） |
| **数据** | akshare 获取 + 内存缓存 | 同左（增加批量预取优化） |

- 双池输出：Top 5（E层展示）+ Top 20% 保底 5（C层 MAD 基准）
- 自适应降级：同行数 < 10 合并双池，< 5 跳过 MAD

### B层：提取财务数据

#### B0：表头语义指引（LLM）
- 横/纵双向布局自动检测（row_major vs column_major）、多级合并表头解析（3-4行）、英文变体匹配（港股/美股）、银行/保险/券商特殊科目
- v3 优化：预建 variant→standard_name 反向索引，模糊匹配从 O(fields×variants) 降为 O(1) 查找

#### B1：代码精确提取（Pandas） + 勾稽校验
- 列式（column_major）表格支持、英文格式数字解析（逗号/括号）、Decimal 精确运算、交叉验证自动修正
- v3 优化：预建行文本索引（row_index），提取复杂度从 O(rows×fields) 降为 O(rows+fields)
- 校验失败 → 跳过 B+/C/D，直出 E 层阻断报告

### B+层：逻辑检查（三步框架 + 6 分制风险等级）

**三步框架**（纯代码，所有步骤独立，不下定论只出清单）：

**Step 1 — 数据层异常扫描**（吴林港2022）：净现比/收现比（行业 p75 + 规模修正）、存贷双高（同规模 p75）、母子资金分离度（均值+2σ）。阈值从 `config/industry_thresholds.yaml` 按同花顺三级行业加载，28 个行业各有专属分位数基准。企业规模自动检测（总资产分大/中/小型），阈值自动缩放宽严。

**Step 2 — 经营行为验证**（张宁珊2017）：销售增长 vs 现金流背离检测、过度投资检测（国企/民企分开判——国企看投资支出扩张、民企看现金流恶化+应收/存货堆积+利润率下降）。内置 `_detect_soe()` 从公司名称+行业标签判断企业性质。

**Step 3 — 内控质量评估**（朱亚萍2015）：4 项定性检查——独立内部审计、现金流预警系统、定期经营现金流分析报告、不相容职务分离。从年报 MD&A 文本中关键词检测，输出缺失项数和评级。

**扩展 7 项**（`fraud_patterns.py`，参考 Beneish M-Score/Dechow F-Score）：应收增速异常、毛利率波动、研发资本化过高、存货堆积、关联交易异常、非经常性损益占比过高、"洗大澡"嫌疑。

**6 分制风险等级**：`data_score(0-3) + behavior_score(0-2) + ic_score(0-2)` → 正常/低风险/中风险/高风险，附带操作建议。结果写入 `ctx.warnings`。`calc_severity()` 升级为行业感知（industry-aware）阈值。

### C层：MAD 偏差异常检测（纯数学计算）
- 算法：中位数绝对偏差 MAD = `median(|x_i - median(x)|)`，比标准差更抗极端值
- 偏离倍数 = `(实际值 - 同行中位数) / MAD`
- 输出：`list[DeviationAnomaly]`（指标 / 实际值 / 偏离倍数 / 严重度）
- 支持按阈值筛选（默认 ≥ 2.0 倍）和按严重度排序

### D层：原因查找与概率分配（LLM 主力）

#### D1：双路径推理（v5.1 严格隔离重构）

| | 路1（原文搬运） | 路2（外部推演） |
|---|---|---|
| **可用知识** | raw_doc + 异常清单（全量） | 异常清单 + hard_tags + A0宏观事实 + business_desc + LLM常识 |
| **禁用** | 自身常识、宏观事实、行业标签、财务画像 | 年报原文、financial_profile |
| **LLM 角色** | 纯搬运：原文说啥搬啥，不说就不说 | 发散式：基于外部情报+常识推演假设 |
| **排序规则** | 显要程度（篇幅最长/位置最前→rank=1） | 来源权威性×共识强度（券商>新闻>股吧） |
| **输出格式** | 统一 JSON：`{anomaly_indicator, explanations[{explanation, confidence_rank, sources}]}` | 同左 |
| **错误处理** | ❌ 不做降级，异常传播全流程中断 | ❌ 同左 |

- LLM 不可用时无模板降级（v5.1 删除 `_template_hypotheses` / `_rule_based_lookup`）

#### D2：概率分配（D-S 证据理论）
- 废弃旧 LLM 概率分配，改用 **Dempster-Shafer 证据理论**
- **Step 0**：LLM 语义合并 + 冲突检测（轻量调用，合并同义归因，标记互斥）
- **Step 1**：构建路1 mass m₁ — ε₁=0.35 固定；k=1→0.65, k=2→73开, 空→全给 Θ
- **Step 2**：构建路2 mass m₂ — ε₂=0.30 固定；取 consensus_weight 前 5 名归一化后 ×0.70
- **Step 3**：Dempster 合成 — K>0.6 标注"严重冲突"但不中断
- **Step 4**：Pignistic 转概率 — m(Θ) 按比例分摊到各原因，保证 Σ=1.0
- **输出**：`ProbabilityAssignment` 含 `ds_metadata`（m1/m2/mass_final/conflict_K）

### E层：最终输出打分与报告

#### E1：综合置信度打分
- 基础分 100，双层扣分：C层（偏离度放大：`实际权重 × (1 + MAD/3)`）+ B+层（造假权重×严重度修正）
- 最终得分 = `100 - (C层扣分 + B+层扣分)`，最低 0 分

#### E2：结构化报告生成
- 四个模块：整体研判（含 Top 5 横向对比）、核心异常与概率归因（含页码/附注）、多空逻辑栈、横向对比表

---

## 三、系统规则与约束

### 数据流向
```
第0层 (PDF → JSON)
   ├─→ A0 (联网搜索宏观情报) + A1 (纯代码查硬标签) → A2 (余弦相似度匹配同行)
   ├─→ B0 (LLM 认表头) → B1 (代码取数 + 勾稽校验)
   │      └─ 校验失败 → 直出 E 层阻断报告
   │      └─ 校验成功 → B+ (基础3+扩展7) + C (MAD偏差)
   └─→ D1 (路1 查原文 + 路2 外部推演) → D2 (概率分配) → E1 (打分) → E2 (报告)
```

### 数据契约（PipelineContext）
| 字段 | 写入层 | 读取层 |
|------|--------|--------|
| raw_doc | 第0层 | A / B / D1路1 |
| macro_facts | A0 | D1路2 |
| tags(hard_tags) | A1 | A2 / D1路2 / E |
| financial_profile | A2 | D1路2 禁用 |
| benchmark | A2 | C / E |
| financials | B | B+ / C |
| logic_anomalies | B+ | D |
| deviations | C | D |
| reasoning_results | D | E |
| score / report | E | 输出 |

### Pipeline 执行顺序
`layer_0 → layer_a → layer_b → layer_bplus → layer_c → layer_d → layer_e`

各层通过 `@registry.register("layer_xx")` 注册，Orchestrator 自动调度，支持跳过任意步骤。

### LLM 调用规范
- 统一网关 `llm/client.py`（LLMClient），Anthropic 主 + OpenAI 备
- Prompt 模板：YAML + Jinja2，位于 `config/prompts/`（7 个文件）
- 调用方式：`client.chat("prompt_name", {变量字典})`
- 指数退避重试，鉴权失败不重试，支持结构化输出（Pydantic schema）
- LLM 响应缓存：SQLite 缓存，默认 TTL 24 小时

### v5.1 重要变更

| 层次 | 变更项 | v5.0 → v5.1 |
|------|--------|-------------|
| **A0** | 冗余代码 | `_extract_materials()` + `_extract_downstream()` 已删除 |
| **A2** | 匹配算法 | 离散等级 → 连续值 `(x-median)/IQR` 稳健标准化 |
| **A2** | 行业池 | 三级行业 → 二级行业（样本更足） |
| **tags.py** | FinancialProfile | `levels: list[str]` → `values: list[float]` |
| **tags.py** | 展示等级 | `to_readable_levels()` 整条调用链已删除 |
| **config** | industry_tags.yaml | 已删除（匹配不再依赖 YAML 阈值） |
| **config** | 行业基准 | **新增** `industry_thresholds.yaml`（28 个行业专属分位数阈值） |
| **config** | 字段映射 | `financial_fields.yaml` 增加 `Revenue_Prior_Year`、`Operating_Profit` |
| **D1路1** | 知识源 | 全量原文+异常清单，严令禁止自身常识/发散 |
| **D1路2** | 知识源 | 移除 financial_profile，新增 business_desc |
| **D1** | 错误处理 | 模板降级 → 异常传播全流程中断 |
| **D1** | 排序规则 | 无 → 路1显要程度 / 路2权威性×共识 |
| **B0** | 表头检测 | 纵向 → 横纵双向+多级嵌套+英文+金融科目 |
| **B0** | 模糊匹配 | **v3 优化**：预建变体索引，O(1) 查找替代 O(fields×variants) |
| **B1** | 数值提取 | 基础 → 列式布局+交叉验证+Decimal 精确运算 |
| **B1** | 行文本索引 | **v3 优化**：预建 row_index，O(rows+fields) 替代 O(rows×fields) |
| **B+** | 框架重构 | 规则清单 → **三步框架**（数据异常+经营行为+内控质量）+ 6 分制风险等级 |
| **B+** | 阈值体系 | 全局固定阈值 → **行业分位数**（28 个行业 p25/p75）+ 企业规模修正 |
| **B+** | 内控评估 | 无 → **Step 3 内控质量**（4 项定性检测，朱亚萍2015） |
| **B+** | 经营行为 | 无 → **Step 2 经营行为验证**（过度投资/销售背离，国企民企分判） |
| **B+** | 企业性质 | 无 → **国企/民企自动检测**（`_detect_soe`） |
| **B+** | 严重度计算 | `calc_severity()` 行业无感知 → **行业感知**（industry-aware） |
| **B+** | 扩展检测 | 无 → `fraud_patterns.py` 新增 7 项造假模式 |
| **第0层** | OCR 纠错 | 无 → `text_corrector.py` 新增 |
| **第0层** | OCR 性能 | 逐个 replace → **编译正则单次扫描 O(n)** |
| **schemas/reasoning.py** | 排序字段 | Explanation/Hypothesis 增加 confidence_rank |
| **schemas/reasoning.py** | D-S 元数据 | ProbabilityAssignment 新增 ds_metadata 字段 |
| **D1** | 输出上限 | 两路 5 条上限（lookups[:5] / hypotheses[:5]） |
| **D2** | 概率算法 | LLM 概率分配 → **D-S 证据理论**（Dempster-Shafer） |
| **D2** | 参数体系 | 硬编码70/30 → **ε₁=0.35, ε₂=0.30, K>0.6 冲突标注** |
| **D2** | 路2 权重 | 无 → **consensus_weight = source_authority × source_count_bonus** |
| **D2** | prompt | d2_probability.yaml → **d2_merge_conflict.yaml**（语义合并+冲突检测） |
| **测试** | 数量 | 51 tests → **83 tests**（新增 32 个 D2 D-S 测试） |

---

## 四、项目目录结构（核心框架）

```
ai-finance-analyzer/
├── config/
│   ├── weights.yaml, thresholds.yaml, financial_fields.yaml
│   ├── industry_thresholds.yaml   # ✨ v5.1 新增：28 行业分位数基准
│   └── prompts/           # 7 个 LLM prompt 模板 (YAML+Jinja2)
├── schemas/               # 10 个 Pydantic v2 数据契约
├── pipeline/              # 全流程编排（Orchestrator + PipelineContext + StepRegistry）
├── layers/
│   ├── layer_0_extract/   # pdf_parser + chunker + text_corrector + merger
│   ├── layer_a_benchmark/ # a0_macro_search + a1_tagging + a2_matcher
│   ├── layer_b_extract/   # b0_semantic_guide + b1_extractors + b1_validators
│   ├── layer_bplus_internal/ # logic_checks（三步框架）+ fraud_patterns
│   ├── layer_c_deviation/ # mad_calculator + deviation_scorer
│   ├── layer_d_reasoning/ # d1_dual_path + d1_lookup_notes + d1_hypothesis + d2_probability
│   └── layer_e_output/    # e1_scoring + e2_report_gen
├── llm/                   # LLM 统一网关（client + prompt_loader + response_parser + cache）
├── data/                  # 同花顺行业分类 CSV + 原始/处理数据
├── tests/                 # 51 tests all passed
├── doc/                   # 架构文档 + 进度 + 任务分配
└── scripts/               # 运行/分析/验证脚本
```
