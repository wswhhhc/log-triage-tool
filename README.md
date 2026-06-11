# 日志异常分诊工具

轻量级日志异常分诊 MVP，用于从多来源、字段不统一、包含脏数据的日志中识别异常、合并同一根因问题、计算优先级、给出处理建议，并判断哪些问题需要人工介入。

![CI](https://github.com/wswhhhc/log-triage-tool/actions/workflows/ci.yml/badge.svg)

## 功能概览

- 读取 JSONL 日志文件，容错处理无效 JSON 行
- 标准化不同来源的字段名，例如 `timestamp/time/created_at`、`service/source/app/system`
- 识别异常日志，包括错误等级、异常关键词和严重脏数据
- 基于 fingerprint 合并同一根因异常
- 按多因素评分生成 P0/P1/P2/P3 优先级
- 给出处理建议，并判断是否需要人工介入
- 支持异常状态更新：`open`、`investigating`、`mitigated`、`resolved`、`ignored`
- 提供 Web 页面、统计卡片、异常详情和上传入口
- 使用 pytest 覆盖核心判断逻辑，并配置 GitHub Actions CI

## 样例数据

项目自带主样例：

- `data/sample_logs.jsonl`
- 共 54 行日志
- 覆盖 11 个来源系统
- 包含字段不一致、时间乱序、脏数据、中英文混杂、动态 ID、重复失败和未知错误

另提供一个较小的上传测试文件：

- `data/upload_test_logs.jsonl`
- 用于快速验证页面上传和重新分析功能

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动服务

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 3. 打开页面

访问：

```text
http://localhost:8000
```

启动后系统会自动读取 `data/sample_logs.jsonl` 并展示分析结果。

## 上传自定义日志

页面支持上传 `.jsonl`、`.json` 或 `.log` 文件。上传后系统会清空上一次分析结果，并重新执行完整流程：

```text
解析 -> 标准化 -> 异常识别 -> 分类 -> 合并 -> 优先级 -> 建议 -> 统计
```

也可以通过 API 上传：

```bash
curl -X POST "http://localhost:8000/api/upload" -F "file=@data/upload_test_logs.jsonl"
```

## 运行测试

```bash
pytest tests/ -v
```

当前测试覆盖 40 个用例，重点验证核心判断逻辑，而不是只测试页面是否渲染。

GitHub Actions CI 会在 push 和 pull request 时自动运行同一组测试。

## API 简介

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | Web 页面 |
| `GET` | `/api/issues` | 获取异常问题列表 |
| `GET` | `/api/issues/{issue_id}` | 获取异常详情 |
| `PUT` | `/api/issues/{issue_id}/status?status=resolved` | 更新异常状态 |
| `GET` | `/api/stats` | 获取整体统计 |
| `POST` | `/api/upload` | 上传日志文件并重新分析 |

## 核心设计

### 如何定义“同一个异常”

同一个异常通过 fingerprint 判定：

```text
fingerprint = MD5(来源系统 | 异常类型 | 标准化消息模板)
```

标准化消息模板包括两步处理：

1. 去除动态内容：订单号、UUID、IP、用户 ID、时间戳、金额、batch ID 等会被替换为占位符。
2. 同义词归一化：例如 `timeout/超时/timed out -> timeout`，`database/数据库/db -> database`。

这样，同一来源、同一根因的异常即使包含不同订单号或中英文不同表述，也能生成相同 fingerprint。

### 如何做异常分类

分类基于关键词匹配和优先顺序：

| 类型 | 示例关键词 |
|------|------------|
| `TIMEOUT` | timeout、超时、timed out |
| `AUTH` | auth、认证、token、permission、权限 |
| `DATABASE` | database、数据库、db、sql、mysql、redis |
| `NETWORK` | network、网络、connection refused、dns、tcp |
| `VALIDATION` | validation、校验、invalid、required、必填 |
| `RESOURCE_LIMIT` | limit、限流、throttle、quota、过载 |
| `DATA_QUALITY` | 可解析但缺少关键字段的脏数据 |
| `UNKNOWN` | 无法匹配到已知规则的问题 |

当一条日志同时命中多个类型时，按规则顺序优先匹配。例如同时包含 `timeout` 和 `database` 时，归为 `TIMEOUT`。

### 如何判断优先级

优先级使用多因素加权评分，满分按 10 分量级设计：

```text
score = 0.30 × severity_score
      + 0.25 × frequency_score
      + 0.25 × core_service_bonus
      + 0.15 × unknown_bonus
      + 0.05 × data_quality_bonus
      + time_span_bonus
```

阈值：

- `P0`：7.0+，立即处理
- `P1`：4.0-6.9，尽快处理
- `P2`：2.0-3.9，正常排队
- `P3`：0-1.9，低影响

核心服务包括 `payment-service`、`order-worker`、`auth-gateway`、`risk-api`。

### 如何判断是否需要人工介入

满足任一条件时需要人工介入：

- `UNKNOWN` 类型异常
- P0/P1 高优先级问题
- 同一问题重复超过 5 次
- P0/P1 且涉及核心服务
- `AUTH` 类型，涉及认证或权限风险

可自动处理的典型情况：

- 低频 timeout
- 低频 network 抖动
- P3 低优先级问题

### 如何处理脏数据

- 无效 JSON 行会被记录为解析阶段脏数据，不会导致程序崩溃。
- 缺少时间戳或消息等关键字段的日志会被标记为脏数据。
- 可解析但字段缺失的日志会进入 `DATA_QUALITY` 类型。
- 完全无效的 JSON 行只进入脏数据统计，不参与异常合并。

## 技术选型

| 组件 | 选择 | 理由 |
|------|------|------|
| 语言 | Python 3 | 适合快速开发和日志数据处理 |
| 后端框架 | FastAPI | 轻量、启动快、API 实现简单 |
| 数据存储 | SQLite | 零配置，适合 MVP 和本地演示 |
| 输入格式 | JSONL | 逐行处理，天然适合日志 |
| 测试框架 | pytest | 语法简洁，适合业务逻辑单元测试 |
| 前端 | HTML + Fetch API | 无构建步骤，便于直接运行 |

## 线上化扩展

如果演进为线上系统，需要补充：

1. SQLite 替换为 PostgreSQL、ClickHouse 或 Elasticsearch
2. 单文件读取改为 Kafka、Filebeat 或 OpenTelemetry 接入
3. fingerprint 和时间字段建立索引，支持增量合并和滑动窗口
4. 分类规则、权重、核心服务列表配置化
5. 增加分页、搜索、过滤、时间范围和图表
6. 增加告警通知、权限控制和审计日志
7. 建立人工反馈闭环，用修正结果持续优化规则
