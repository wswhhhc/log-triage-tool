# 日志异常分诊工具

## 项目简介

一个轻量级日志异常分诊系统，能够自动读取 JSONL 格式日志，识别异常、合并同一根因的重复问题、计算优先级并给出处理建议。核心设计目标是**抗脏数据**——在字段缺失、格式不一致、类型错误、编码异常、中英文混杂等场景下仍能稳定输出有意义的结果。

内置 54 行样本数据，覆盖 11 个来源系统，包含字段不一致、时间乱序、脏数据（无效 JSON / 字段缺失 / 非法类型）、中英文混杂、动态 ID 和未知错误等场景。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动服务（自动处理样本数据）
uvicorn app.main:app --reload --host 127.0.0.1 --port 8001

# 3. 打开浏览器访问
open http://localhost:8001
```

服务启动后会自动加载并分析 `data/sample_logs.jsonl`，页面展示统计看板和异常列表。

### 上传自定义日志

点击页面上传按钮选择 `.jsonl` / `.json` / `.log` 文件，或通过 API：

```bash
curl -X POST "http://localhost:8001/api/upload" -F "file=@your_logs.jsonl"
```

系统会清空前一次结果并重新执行完整分析流程。

## 架构设计

### 处理流水线

```
JSONL ──► Parser ──► Normalizer ──► Classifier ──► Deduplicator ──► Prioritizer ──► Recommender ──► SQLite
                     │                │               │
                  类型校验         脏数据优先        unknown source
                  时间戳解析       关键词分类        自动消歧
                  脏数据标记       单词边界匹配      缩写归一化
```

所有日志（含解析阶段无法转为 dict 的脏数据）经过同一套标准化、异常检测、分类、合并流程，保证统计口径一致，不存在「被标记为异常但无对应 issue」的孤立数据。

### 异常合并：Fingerprint 算法

```
fingerprint = MD5(来源系统 | 异常类型 | 标准化消息模板)
```

**消息标准化**分三层：

1. **动态内容替换**：UUID、IP、订单号、URL、路径、端口、Pod 名、Git hash 等替换为占位符（如 `<ORDER_ID>`、`<UUID>`）
2. **同义词归一化**：`timeout/超时 → timeout`、`database/数据库 → database`、`conn → connection`
3. **去停用词去重**：过滤中英文高频虚词，去除重复 token

### 异常分类规则

按顺序匹配，先到先得：

| 优先级 | 类型 | 部分关键词 |
|--------|------|-----------|
| 1 | TIMEOUT | timeout、超时、timed out |
| 2 | AUTH | auth、token、permission、denied |
| 3 | DATABASE | database、db、sql、mysql、redis |
| 4 | NETWORK | network、connection refused、dns |
| 5 | VALIDATION | validation、invalid、required、参数 |
| 6 | RESOURCE_LIMIT | limit、throttle、quota、queue |
| 7 | DATA_QUALITY | 脏数据（仅当其他类型都不匹配时） |
| 8 | UNKNOWN | 兜底 |

### 优先级评分公式

```
score = 0.30 × severity           # 异常类型严重度 (0-10)
      + 0.25 × frequency          # 对数缩放重复次数 (0-10)
      + 0.25 × core_service       # 核心服务 +10 (精确匹配)
      + 0.15 × unknown_penalty    # UNKNOWN 类型 +10
      + 0.10 × data_quality       # DATA_QUALITY +10
      + time_span_bonus           # 持续超1小时 +1
```

阈值：**P0** ≥ 7.0 / **P1** ≥ 4.0 / **P2** ≥ 2.0 / **P3** < 2.0

核心服务：`payment-service`、`order-worker`、`auth-gateway`、`risk-api`（精确匹配，不支持子串）

## 抗脏数据能力

系统在以下层面防御脏数据：

| 层级 | 防护措施 |
|------|---------|
| **Parser** | 拒绝非 dict JSON（`["list"]` / `null` / `"string"`）；限制文件 100MB / 100 万行 / 单行 10 万字符；UTF-8 BOM 自动去除 |
| **Normalizer** | 类型守卫（source/message 只接受字符串）；时间戳合理性（1900-2100年）；bool 排除（避免 `True` 被误认为 Unix 时间戳）；数值消息支持（`"message": 500`） |
| **Classifier** | 脏数据按内容优先分类；关键词单词边界匹配（`\berror\b` 不匹配 `noerror`）；系统生成消息不被关键词误导 |
| **Deduplicator** | `source="unknown"` 时自动用原始身份字段（service/job/host）hash 消歧，不同服务不会错误合并 |
| **Pipeline** | 事务保护（`BEGIN/ROLLBACK`），任一步骤失败自动回滚；上传接口校验编码和内容格式 |

## 测试覆盖

68 个测试用例，覆盖全部模块：

```
tests/
├── test_parser.py          # 10 用例：非 dict、BOM、超长行、文件不存在
├── test_normalizer.py      # 16 用例：时间戳格式、类型校验、bool 排除、数值消息
├── test_classifier.py      # 14 用例：各类型分类、脏数据优先级、关键词误匹配
├── test_deduplicator.py    # 12 用例：动态内容屏蔽、中英文同义词、unknown source 消歧
├── test_prioritizer.py     # 7 用例：核心服务精确匹配、阈值边界
├── test_recommender.py     # 6 用例：人工介入规则、自动处理判定
└── test_integration.py     # 1 用例：10 种脏数据端到端全流水线
```

```bash
# 运行全部
pytest tests/ -v

# 运行单个
pytest tests/test_integration.py::test_end_to_end_edge_cases -v
```

## 技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| 语言 | Python 3.11 | 快速开发，类型提示完善 |
| 后端 | FastAPI | 原生异步，自动 OpenAPI 文档 |
| 存储 | SQLite (WAL) | 零配置，支持读写并发，9 个索引 |
| 输入 | JSONL | 逐行处理，流式友好 |
| 测试 | pytest | 简洁高效的 assertion |
| 前端 | HTML + Fetch | 无构建步骤，纯静态 |

## 设计决策

详细的设计决策记录在 [`DECISIONS.md`](DECISIONS.md) 和 [`AI_USAGE.md`](AI_USAGE.md) 中，包括：

- 为什么选择 fingerprint 三要素而非全文 hash
- 为什么放弃机器学习聚类方案
- 权重参数的设计依据和手工验算过程
- AI 协助生成代码的部分以及发现并修正的错误
