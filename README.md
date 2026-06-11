# 日志异常分诊工具

## 项目简介

一个轻量级日志异常分诊 MVP，能够自动读取 JSONL 格式日志文件，识别异常、合并同一根因的重复异常、生成优先级排序、提供处理建议，并判断哪些问题需要人工介入。

项目自带样例数据 `data/sample_logs.jsonl`，共 54 行日志，覆盖 11 个来源系统，包含字段不一致、时间乱序、脏数据、中英文混杂、动态 ID 和未知错误等场景。

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动项目

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 3. 访问页面

打开浏览器访问 `http://localhost:8000`，页面会自动加载并展示异常分诊结果。

### 4. 使用自定义日志

页面支持上传 `.jsonl`、`.json` 或 `.log` 文件，上传后会重新执行完整分析流程。也可以直接调用接口：

```bash
curl -X POST "http://localhost:8000/api/upload" -F "file=@data/sample_logs.jsonl"
```

上传或重新处理日志时，系统会先清空上一次分析结果，避免旧数据残留影响统计。

## 运行测试

```bash
pytest tests/ -v
```

## 设计说明

### 如何定义"同一个异常"

同一个异常通过 **fingerprint（指纹）** 来判定。指纹由三部分组成：

```
fingerprint = MD5(来源系统 | 异常类型 | 标准化消息模板)
```

其中**标准化消息模板**的处理分为两步：

1. **去除动态内容**：订单号（`ORD-88271`）、UUID、IP 地址、用户 ID、时间戳、金额、batch ID 等替换为占位符（如 `<ORDER_ID>`、`<IP>`）
2. **同义词归一化**：中英文同义词映射到同一标准词，如 `timeout/超时/timed out → TIMEOUT`、`database/数据库/db → DATABASE`

这样，同一来源、同一根因的异常（即使包含不同订单号、不同语言的表述）会生成相同的 fingerprint，从而实现合并。

### 如何做异常分类

基于关键词匹配 + 优先规则，按以下顺序依次匹配：

| 优先级 | 类型 | 匹配关键词 |
|--------|------|-----------|
| 最高 | TIMEOUT | timeout、超时、timed out |
| 高 | AUTH | auth、认证、token、permission、权限 |
| 高 | DATABASE | database、数据库、db、sql、mysql、redis |
| 中 | NETWORK | network、网络、connection refused、dns、tcp |
| 中 | VALIDATION | validation、校验、invalid、required、必填 |
| 低 | RESOURCE_LIMIT | limit、限流、throttle、quota、过载 |
| 最低 | DATA_QUALITY | 脏数据、字段缺失 |
| 兜底 | UNKNOWN | 以上都不匹配 |

**分类优先级规则**：先匹配到的优先。例如一条日志同时包含 "timeout" 和 "database"，归为 TIMEOUT 而非 DATABASE。

### 如何判断优先级

使用**多因素加权评分公式**，满分为 10 分：

```
score = 0.30 × severity_score     # 严重程度评分（0-10）
      + 0.25 × frequency_score    # 重复次数评分（对数缩放，0-10）
      + 0.25 × core_service_bonus # 核心链路加分（0 或 10）
      + 0.15 × unknown_bonus      # 未知错误加分（0 或 10）
      + 0.05 × data_quality_bonus # 数据质量加分（0 或 10）
      + time_span_bonus           # 持续发生超过1小时额外+1
```

**阈值映射：**
- 7.0+ → **P0**（立即处理）
- 4.0-6.9 → **P1**（尽快处理）
- 2.0-3.9 → **P2**（正常排队）
- 0-1.9 → **P3**（低影响）

**核心服务**包括：payment-service、order-worker、auth-gateway、risk-api。

### 如何判断是否需要人工介入

**需要人工介入（满足任一）：**
- UNKNOWN 类型异常（无法自动分类）
- P0/P1 高优先级问题
- 同一问题重复超过 5 次
- P0/P1 问题且涉及核心服务
- AUTH 类型（涉及认证安全）

**可自动处理（同时满足）：**
- 没有以上需要人工的条件
- 低频 TIMEOUT（发生 ≤ 2 次）
- 低频 NETWORK 抖动（发生 ≤ 2 次）
- P3 低优先级

### 如何处理脏数据

1. **解析阶段**：无效 JSON 行被捕获并记录行号和内容前 200 字符，程序不崩溃
2. **标准化阶段**：缺少关键字段（时间戳、消息）被标记为脏数据，记录具体原因
3. **统计阶段**：脏数据独立统计并在前端展示
4. **分类阶段**：可解析但字段缺失的日志会进入 DATA_QUALITY 类型；完全无效的 JSON 行只进入脏数据统计，不参与异常合并

## 技术选型

| 组件 | 选择 | 理由 |
|------|------|------|
| 语言 | Python 3 | 快速开发，生态丰富，适合数据处理 |
| 后端框架 | FastAPI | 原生异步支持，自动 API 文档，部署简单 |
| 数据存储 | SQLite | 零配置，单文件，适合 MVP 规模 |
| 输入格式 | JSONL | 逐行处理，流式友好，不要求加载全部到内存 |
| 测试框架 | pytest | 简洁高效，fixture 灵活 |
| 前端 | HTML + Fetch API | 无构建步骤，纯静态，功能完整 |

## 线上化扩展

如果这是线上系统，还需要补充以下内容：

1. **存储**：SQLite → PostgreSQL/ClickHouse，支持高并发和数据持久化
2. **数据接入**：单文件读取 → 流式 Kafka 消费，支持实时处理
3. **指纹计算**：建立时间索引和 fingerprint 索引，支持增量更新和滑动窗口
4. **前端**：需要分页、搜索、过滤、时间范围选择、图表可视化
5. **配置管理**：分类规则、权重参数配置化和版本管理
6. **可观测性**：添加处理延迟、吞吐量、准确率等指标和告警
7. **告警集成**：对接企业微信、钉钉、PagerDuty 等通知渠道
8. **反馈闭环**：人工标记修正后，反馈到规则库实现自我改进
