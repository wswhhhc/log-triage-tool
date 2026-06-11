# 设计决策说明

## 对需求的假设

- **核心业务链路**：包括 payment-service、order-worker、auth-gateway、risk-api。这些服务出现问题会影响资金安全和核心交易流程
- **数据时效性**：日志是准实时的，但可能时间乱序（模拟数据中 timestamp 不按顺序分布）
- **用户画像**：使用该工具的是技术人员（SRE/运维/开发），不需要复杂的 UI，但需要看到每个决策的推理过程
- **日志量级**：当前假设单次处理批量日志（数十到数百条），不是持续流式接入
- **日志格式**：假设日志以 JSONL 格式提供，字段名有常见变化但不会超出给定映射范围

## 放弃的方案

### 放弃方案 1: 用机器学习做异常聚类
- **原因：** 不可解释，需要大量标注训练数据，黑盒决策无法满足"可解释性"的评估要求
- **选择方案：** 基于规则的 fingerprint 合并，每条合并都有明确推理链

### 放弃方案 2: 用 Elasticsearch 做日志存储
- **原因：** 太重，部署复杂，偏离 MVP 定位
- **选择方案：** SQLite + JSONL，零配置，单文件存储

### 放弃方案 3: React/Vue 做前端
- **原因：** 过度设计，需要 npm 构建、打包，不符合时间预算
- **选择方案：** 原生 HTML + Fetch API，纯静态，功能完整

### 放弃方案 4: 直接按日志等级映射优先级
- **原因：** 过于简单，无法体现"多因素加权"的评估要求
- **选择方案：** 多维度加权评分（严重程度 + 频率 + 核心链路 + 未知惩罚）

## 异常合并规则

### Fingerprint 算法

```python
fingerprint = MD5(来源系统 | 异常类型 | 标准化消息模板)
```

### 消息预处理流程

1. **动态内容替换**（按顺序）：
   - UUID → `<UUID>`
   - 日期时间 → `<DATETIME>`
   - IP 地址 → `<IP>`
   - 订单号（`order_id=ORD-*`, `ORD-*`, `order=*`） → `<ORDER_ID>`
   - 用户 ID（`user_*`, `user_id=*`） → `<USER_ID>`
   - Batch ID → `<BATCH_ID>`
   - 长数字 ID（≥6 位） → `<NUM_ID>`
   - 数字参数（`=` 后面的值） → `<NUM>`
   - 持续时间（如 `30000ms`） → `<DURATION>`
   - retry N/M → `retry <N>/<N>`
   - Trace ID → `<TRACE_ID>`
   - 金额 → `<AMOUNT>`
   - URL 中的数字路径 → `<ID>`

2. **同义词归一化**：
   - `timeout/timed out/超时` → `timeout`
   - `database/db/mysql/postgres/redis/数据库` → `database`
   - `failed/failure/失败` → `failed`
   - `permission denied/unauthorized/forbidden/auth/无权限/未授权/认证` → `auth`
   - `connection refused/network/dns/socket/连接被拒绝/网络` → `network`
   - `rate limit/throttle/quota/限流/配额` → `resource_limit`

3. **去重**：同义词替换后可能出现重复 token，使用 order-preserving unique 去重

### 为什么不直接用消息全文 hash

如果直接哈希消息原文，`"timeout for order ORD-88271"` 和 `"timeout for order ORD-99231"` 会产生完全不同的哈希值，即使它们本质上是同一个问题。fingerprint 的价值就是在哈希前消除这些表层差异。

## 优先级规则

### 评分公式

```python
score = 0.30 × severity_score     # 基于异常类型
      + 0.25 × frequency_score    # log2(1+count) × 2.5, 上限 10
      + 0.25 × core_service_bonus # 核心链路 = 10, 否则 = 0
      + 0.15 × unknown_bonus      # UNKNOWN = 10, 否则 = 0
      + 0.05 × data_quality_bonus # DATA_QUALITY = 10, 否则 = 0
      + time_span_bonus           # 持续 > 1 小时 = 1, 否则 = 0
```

### 严重程度评分

| 异常类型 | 分数 | 理由 |
|---------|------|------|
| DATABASE | 8 | 数据库通常是核心依赖，影响面广 |
| UNKNOWN | 9 | 未知问题需要最高级警惕 |
| AUTH | 7 | 认证问题可能涉及安全 |
| TIMEOUT | 5 | 中等严重，可能影响用户体验 |
| NETWORK | 4 | 网络问题通常有自动恢复机制 |
| RESOURCE_LIMIT | 3 | 通常可自动恢复或扩容 |
| VALIDATION | 2 | 通常是上游数据问题 |
| DATA_QUALITY | 1 | 不是业务异常，优先级最低 |

### 阈值设计说明

阈值设置为 P0 ≥ 7、P1 ≥ 4、P2 ≥ 2、P3 < 2。以 DATABASE 核心服务重复 10 次为例：
- severity: 0.30 × 8 = 2.4
- frequency: 0.25 × 10 = 2.5
- core_service: 0.25 × 10 = 2.5
- 总分 ≈ 7.4 → P0

这意味着"核心数据库大量重复异常"能被正确识别为最高优先级。

## AI vs 自己决定

| 决策 | 谁做的 |
|------|--------|
| 技术栈选择（Python + FastAPI + SQLite） | AI 建议，我确认 |
| 项目模块划分（7 个核心模块） | AI 建议，我调整 |
| Fingerprint 算法设计 | **我自己设计** |
| 优先级评分公式和权重 | **我自己定义** |
| 分类关键词（中英文） | AI 生成，我补充了关键缺失词 |
| 人工介入判断规则 | **我自己定义** |
| 动态内容替换正则模式 | **我自己设计** |
| 测试用例边界条件 | AI 提供框架，我补充边界 |
| 前端 UI 设计 | AI 生成，我调整 |
| 脏数据处理策略 | **我自己决定** |
| 数据结构模型 | AI 建议，我增加了 Stats 字段 |

## 错误修正机制

如果分类或优先级判断错了，系统通过以下方式被发现和修正：

1. **人工查看**：所有异常问题的详情中保留原始日志和推理过程，技术人员可以查看判断是否正确
2. **状态覆盖**：技术人员可以通过前端操作将异常状态从 open 改为 investigating/mitigated/resolved/ignored，覆盖自动判断
3. **规则扩展**：如果某类异常被错误归类到 UNKNOWN，可以在 classifier.py 的 ANOMALY_KEYWORDS 中添加关键词
4. **权重调整**：如果优先级普遍偏高或偏低，可以调整 prioritizer.py 中的 WEIGHTS 和阈值
5. **反馈闭环（长期）**：当人工修正积累到一定量后，可以用修正数据来优化规则

## 规模扩展

如果日志量从 50 条变成 30 万条，当前方案需要以下调整：

1. **存储**：SQLite → PostgreSQL/ClickHouse，支持高并发和列式查询
2. **数据接入**：单文件读取 → 流式 Kafka 消费，支持实时处理
3. **指纹计算**：需要布隆过滤器做近似去重，时间索引 + fingerprint 索引支持滑动窗口
4. **合并算法**：当前是批量处理，需要改为增量更新（新日志流入 → 检查是否匹配已有 fingerprint → 合并或新建）
5. **前端**：需要分页（当前一次性返回所有问题）、搜索过滤、时间范围选择
6. **配置管理**：权重、分类关键词、核心服务列表需要外部化到配置文件
7. **可观测性**：添加处理延迟（p50/p99）、吞吐量、误报/漏报率等指标
8. **规则热更新**：修改分类关键词或权重后不重启服务即可生效
