# 日志异常分诊工具

从多来源、字段不统一、包含脏数据的日志中自动识别异常、合并同一根因问题、计算优先级并给出处理建议。

- 支持 11 个来源系统，覆盖 8 种异常类型，处理 54 条含脏数据的混合日志
- 基于 fingerprint 的根因合并，能区分"同一错误不同订单号"和"不同根因相似文案"
- 6 条规则判断是否需要人工介入，3 条规则判断可自动处理的场景
- 支持上传自定义日志文件并实时重跑完整分析流程

![CI](https://github.com/wswhhhc/log-triage-tool/actions/workflows/ci.yml/badge.svg)

## 项目结构

```
├── app/
│   ├── main.py          # FastAPI 应用 + 流水线编排
│   ├── models.py        # 数据模型（LogEntry/AnomalyIssue/Stats 等）
│   ├── parser.py        # JSONL 解析，容错无效行
│   ├── normalizer.py    # 多来源字段名标准化 + 脏数据检测
│   ├── classifier.py    # 异常识别 + 关键词分类
│   ├── deduplicator.py  # fingerprint 合并 + 消息归一化
│   ├── prioritizer.py   # 多因素加权优先级评分
│   ├── recommender.py   # 处理建议 + 人工介入判断
│   └── storage.py       # SQLite CRUD
├── tests/               # 40 个测试用例，覆盖核心判断逻辑
├── data/                # 内置样例日志
├── static/              # 前端页面
└── requirements.txt
```

## 快速开始

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

打开 `http://localhost:8000`，页面会自动加载 `data/sample_logs.jsonl` 并展示分析结果（统计卡片 + 异常列表 + 每条异常的推理过程）。

## 核心设计

### Fingerprint 合并

```
fingerprint = MD5(来源系统 | 异常类型 | 标准化消息模板)
```

标准化消息模板经过两层处理：

1. **动态内容替换** — UUID、订单号、IP、金额等替换为占位符，避免同一错误因参数不同被拆成多条
2. **同义词归一化** — 中英文同义词统一为同一 token（timeout/超时 → timeout）

同一来源、同一根因的异常即使包含不同参数或中英文混用，也能生成相同的 fingerprint。

### 分类规则

按关键词顺序匹配，同时命中多个时以先匹配的为准：

| 类型 | 关键词 |
|------|--------|
| TIMEOUT | timeout、超时、timed out |
| AUTH | auth、认证、token、permission、权限 |
| DATABASE | database、db、sql、mysql、redis |
| NETWORK | network、connection refused、dns、tcp |
| VALIDATION | validation、invalid、必填、格式 |
| RESOURCE_LIMIT | limit、throttle、quota、过载 |
| DATA_QUALITY | 可解析但缺少关键字段的脏数据 |
| UNKNOWN | 无法匹配已知规则 |

### 优先级评分

多因素加权公式，满分 10 分：

- 严重程度（基于异常类型）× 0.30
- 重复频率（对数缩放）× 0.25
- 核心服务加分 × 0.25
- 未知类型惩罚 × 0.15
- 数据质量加分 × 0.05
- 持续超 1 小时额外 +1.0

阈值：P0 ≥ 7.0（立即处理）、P1 ≥ 4.0（尽快处理）、P2 ≥ 2.0（正常排队）、P3 < 2.0（低影响）

核心服务：payment-service、order-worker、auth-gateway、risk-api

### 人工介入判断

需要人工介入（任一满足）：UNKNOWN 类型、P0/P1 高优先级、同一问题重复超过 5 次、P0/P1 涉及核心服务、AUTH 类型认证安全风险。

可自动处理（同时满足）：低频 timeout/network 抖动可自动重试、P3 低优先级。

### 脏数据处理

- 无效 JSON 行不会导致程序崩溃，记录为解析阶段脏数据
- 缺少时间戳或消息过短的日志被标记为脏数据并记录原因
- 可解析但字段缺失的日志归入 DATA_QUALITY 类型

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | Web 页面（统计卡片 + 异常列表 + 详情） |
| GET | `/api/issues` | 获取异常问题列表 |
| GET | `/api/issues/{id}` | 获取异常详情 |
| PUT | `/api/issues/{id}/status?status=resolved` | 更新异常状态 |
| GET | `/api/stats` | 获取整体统计 |
| POST | `/api/upload` | 上传日志文件并重新分析 |

## 运行测试

```bash
pytest tests/ -v
```

## 技术选型

Python 3 + FastAPI + SQLite + pytest，纯 HTML 前端（无构建步骤）。

详细设计决策和 AI 协作记录见 [DECISIONS.md](DECISIONS.md) 和 [AI_USAGE.md](AI_USAGE.md)。
