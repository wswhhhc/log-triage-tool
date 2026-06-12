# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

日志异常分诊工具（Log Anomaly Triage Tool）— 轻量级 MVP，从多来源、字段不统一、包含脏数据的日志中识别异常、合并同一根因问题、计算优先级、给出处理建议。

技术栈：Python 3.11+ / FastAPI / SQLite (WAL mode) / JSONL 输入。

## 常用命令

```bash
# 安装依赖
pip install -r requirements.txt

# 启动开发服务（热重载）
uvicorn app.main:app --reload --host 127.0.0.1 --port 8001

# 运行全部测试（68 个用例）
pytest tests/ -v

# 运行单个测试文件
pytest tests/test_classifier.py -v

# 运行单个测试用例
pytest tests/test_normalizer.py::test_timestamp_bool_not_confused -v

# 全流水线集成测试（含脏数据场景）
pytest tests/test_integration.py::test_end_to_end_edge_cases -v
```

## 架构概览

### 处理流水线

```
JSONL 文件
   │
   ▼
Parser ──── 非 dict / 无效 JSON / 超长行 → 标记脏数据并继续
   │
   ▼
Normalizer ── 字段映射、类型校验、时间戳解析（含时区偏移）
   │            来源缺失 / 消息过短 → is_dirty=True
   │
   ▼
Classifier ── is_anomaly 判定 + classify 分类
   │            TIMEOUT > AUTH > DATABASE > NETWORK >
   │            VALIDATION > RESOURCE_LIMIT > DATA_QUALITY > UNKNOWN
   │
   ▼
Deduplicator ── fingerprint = MD5(来源 | 类型 | 标准化消息)
   │             动态内容屏蔽 + 中英文同义词归一化 + 缩写扩展
   │             source=unknown 时自动用原始内容 hash 消歧
   │
   ▼
Prioritizer ── 多因素加权评分 → P0/P1/P2/P3
   │
   ▼
Recommender ── 人工介入判定 + 处理建议
   │
   ▼
Storage ── SQLite (WAL), 9 个索引, 事务保护
```

### 模块职责

| 模块 | 文件 | 核心职责 |
|------|------|---------|
| Parser | `app/parser.py` | 读取 JSONL，容错处理无效 JSON/非 dict/超长行/超多行/BOM，返回(有效日志, 脏数据) |
| Normalizer | `app/normalizer.py` | 字段名映射、类型校验、时间戳解析（ISO 8601/Unix/时区偏移）、脏数据标记 |
| Classifier | `app/classifier.py` | is_anomaly 判定（等级/关键词/脏标记）、classify 分类（8 种类型） |
| Deduplicator | `app/deduplicator.py` | Message 标准化（动态内容替换 + 同义词归一化 + 缩写扩展）、fingerprint 生成、异常合并 |
| Prioritizer | `app/prioritizer.py` | 加权评分（严重程度×0.30 + 频率×0.25 + 核心服务×0.25 + 未知×0.15 + 数据质量×0.10 + 时间加分） |
| Recommender | `app/recommender.py` | 人工介入判定规则、处理建议生成 |
| Storage | `app/storage.py` | SQLite CRUD、WAL 模式启动、安全序列化、9 个索引 |
| Models | `app/models.py` | 枚举（LogLevel/AnomalyType/Priority/IssueStatus）+ 数据类（LogEntry/AnomalyIssue/Stats） |
| Main | `app/main.py` | FastAPI 应用、流水线编排（事务保护）、上传校验、启动时处理样本数据 |

### 抗脏数据架构

所有日志（含解析阶段的脏数据）通过同一标准化→异常检测→分类→合并流程，保证数据一致性：

1. **Parser 层**：拒绝非 dict 结构（`["list"]`/`null`/`"string"`），限制文件大小（100MB）/行数（100万）/单行长度（10万字符），UTF-8 BOM 自动去除（`utf-8-sig`）
2. **Normalizer 层**：类型守卫（`_extract_first` 只接受 `str`），时间戳合理性校验（1900-2100年），bool 排除（避免 `True` 被误认为 Unix 时间戳），数值消息支持（`"message": 500`）
3. **Classifier 层**：脏数据按内容优先分类，仅当无法匹配任何类型时才降为 `DATA_QUALITY`；关键词单词边界匹配（`\berror\b` 不匹配 `noerror`）
4. **Deduplicator 层**：`source="unknown"` 时用原始内容签名（service/job/host 等字段 hash）消歧，避免不同服务被错误合并
5. **Pipeline 层**：事务包装（`BEGIN/ROLLBACK`），任一步骤失败自动回滚，旧数据不丢失

### 关键设计点

- **Fingerprint 算法**：`MD5(来源系统 | 异常类型 | 标准化消息模板)`，标准化包含动态内容替换（UUID/IP/订单号/URL/路径/端口/Pod/Hash → 占位符）和同义词归一化（timeout/超时 → timeout）以及缩写扩展（conn→connection, err→error）
- **分类优先级**：TIMEOUT > AUTH > DATABASE > NETWORK > VALIDATION > RESOURCE_LIMIT > DATA_QUALITY > UNKNOWN（关键字检查顺序决定覆盖关系）
- **优先级阈值**：P0 ≥ 7.0, P1 ≥ 4.0, P2 ≥ 2.0, P3 < 2.0（满分 10 分量级）。核心服务精确匹配（Set lookup），不支持子串匹配
- **核心服务**：payment-service, order-worker, auth-gateway, risk-api
- **批量处理**：每次启动或上传新文件都清空旧数据再重跑，非增量模式
- **存储**：SQLite WAL 模式，9 个索引（logs: source/level/is_dirty/is_anomaly; issues: anomaly_type/priority/status/source/fingerprint）

### 测试组织

68 个测试覆盖全部模块：

| 文件 | 用例数 | 重点覆盖 |
|------|--------|---------|
| `test_parser.py` | 10 | 非法 JSON、非 dict、BOM 首行、超长行、文件不存在、空文件 |
| `test_normalizer.py` | 16 | 时间戳格式（ISO/Unix/时区偏移/不合理值/bool）、类型校验、脏标记、数值消息 |
| `test_classifier.py` | 14 | 各类型分类、脏数据优先按内容、关键词误匹配防护 |
| `test_deduplicator.py` | 12 | 动态内容屏蔽、中英文同义词、unknown source 消歧、classified 缺失防御 |
| `test_prioritizer.py` | 7 | 核心服务精确匹配、阈值边界、DATA_QUALITY 权重 |
| `test_recommender.py` | 6 | 人工介入规则、自动处理判定 |
| `test_integration.py` | 1 | 端到端集成：10 种脏数据场景同时跑通全流水线 |
