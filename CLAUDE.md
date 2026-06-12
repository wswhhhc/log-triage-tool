# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

日志异常分诊工具（Log Anomaly Triage Tool）— 轻量级 MVP，从多来源、字段不统一、包含脏数据的日志中识别异常、合并同一根因问题、计算优先级、给出处理建议。

## 常用命令

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 运行全部测试
pytest tests/ -v

# 运行单个测试文件
pytest tests/test_classifier.py -v

# 运行单个测试用例
pytest tests/test_classifier.py::test_classify_timeout_before_database -v
```

## 架构概览

处理流水线（Pipeline）：

```
解析(parser) → 标准化(normalizer) → 异常识别+分类(classifier) → 指纹合并(deduplicator) → 优先级(prioritizer) → 建议(recommender) → 持久化(storage)
```

### 模块职责

| 模块 | 文件 | 职责 |
|------|------|------|
| Parser | `app/parser.py` | 读取 JSONL 文件，容错处理无效 JSON 行，返回(有效日志, 脏数据)元组 |
| Normalizer | `app/normalizer.py` | 统一多来源字段名（timestamp/time/created_at → 标准字段），检测脏数据 |
| Classifier | `app/classifier.py` | 判断是否为异常，按关键词矩阵分类（TIMEOUT/AUTH/DATABASE/NETWORK/VALIDATION/RESOURCE_LIMIT/DATA_QUALITY/UNKNOWN） |
| Deduplicator | `app/deduplicator.py` | 基于 fingerprint = MD5(来源\|类型\|标准化消息模板) 合并同一根因异常；包含动态内容替换和同义词归一化 |
| Prioritizer | `app/prioritizer.py` | 多因素加权评分（严重程度×0.30 + 频率×0.25 + 核心服务×0.25 + 未知惩罚×0.15 + 数据质量×0.05 + 时间跨度加分），映射到 P0/P1/P2/P3 |
| Recommender | `app/recommender.py` | 判断是否需要人工介入（6 条规则），给出处理建议 |
| Storage | `app/storage.py` | SQLite CRUD，包含 logs 和 issues 两张表 |
| Models | `app/models.py` | 数据模型和枚举（LogLevel/AnomalyType/Priority/IssueStatus/LogEntry/AnomalyIssue/Stats） |
| Main | `app/main.py` | FastAPI 应用，API 路由，流水线编排，启动时自动处理 sample_logs.jsonl |

### 关键设计点

- **Fingerprint 算法**：三要素（来源系统 + 异常类型 + 标准化消息模板），标准化包含动态内容替换（UUID/IP/订单号 → 占位符）和同义词归一化（timeout/超时 → timeout）
- **分类优先级**：关键词按顺序匹配，TIMEOUT > AUTH > DATABASE > NETWORK > VALIDATION > RESOURCE_LIMIT > DATA_QUALITY > UNKNOWN
- **优先级阈值**：P0 ≥ 7.0, P1 ≥ 4.0, P2 ≥ 2.0, P3 < 2.0（满分 10 分量级）
- **核心服务**：payment-service, order-worker, auth-gateway, risk-api
- **批量处理**：每次启动或上传新文件都清空旧数据再重跑，非增量模式

### 测试组织

测试集中在 `tests/` 目录，共 40 个用例。每个测试文件对应一个核心模块，重点验证边界条件和判断逻辑而非页面渲染。
