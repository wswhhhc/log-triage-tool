# 日志异常分诊工具 - 项目执行规划

> **跟着本文档做完，你就能交付项目。**

---

## ⚠️ 实施修正点（先看）

下面几项是执行本文档时必须修正的点，避免照抄代码片段后和需求验收不一致：

1. `prioritizer.py` 中不要再执行 `score = score / 10.0`。前面的加权分数已经按 0-10 设计，继续除以 10 会导致 P0/P1/P2 阈值基本失效。
2. `deduplicator.py` 的 `normalize_message()` 不只要去除订单号、UUID、IP 等动态内容，还要做中英文同义词归一化，例如 `timeout/超时/timed out -> TIMEOUT`、`database/数据库/db -> DATABASE`。
3. `test_chinese_english_same_type_same_fingerprint()` 必须有真实断言：`assert fp1 == fp2`。
4. 无效 JSON 行也要计入脏数据统计。实现时可以把 parse 阶段的 `dirty` 作为 `parse_dirty_count` 传入统计，或保存为特殊 dirty log 记录。
5. FastAPI 中不要用 `return {"error": ...}, 404` 这种 Flask 风格返回；应使用 `HTTPException(status_code=404, detail=...)`。
6. `AI_USAGE.md` 和 `DECISIONS.md` 中的示例只能当提纲，不能原样照抄。最终内容必须符合真实开发过程。

---

## 📁 第一步：创建项目结构（10 分钟）

```bash
mkdir -p app data tests
touch app/__init__.py
touch tests/__init__.py
```

最终目录结构：
```
work1/
├── app/
│   ├── __init__.py
│   ├── main.py          ← FastAPI 入口 + 路由
│   ├── models.py        ← 数据模型定义
│   ├── parser.py        ← JSONL 读取 + 脏数据识别
│   ├── normalizer.py    ← 字段标准化
│   ├── classifier.py    ← 异常分类
│   ├── deduplicator.py  ← Fingerprint + 合并
│   ├── prioritizer.py   ← 优先级评分
│   ├── recommender.py   ← 处理建议 + 人工介入判断
│   └── storage.py       ← SQLite 存取
├── data/
│   └── sample_logs.jsonl  ← 模拟日志数据
├── tests/
│   ├── __init__.py
│   ├── test_parser.py
│   ├── test_normalizer.py
│   ├── test_classifier.py
│   ├── test_deduplicator.py
│   ├── test_prioritizer.py
│   └── test_recommender.py
├── static/
│   └── index.html       ← 简单前端页面
├── README.md
├── AI_USAGE.md
├── DECISIONS.md
└── requirements.txt
```

---

## 📦 第二步：创建 requirements.txt

```text
fastapi==0.115.0
uvicorn==0.30.0
pytest==8.3.0
```

然后执行 `pip install -r requirements.txt`

---

## 🧱 第三步：实现 app/models.py

### 3.1 数据模型

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Any

class LogLevel(Enum):
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    FATAL = "fatal"
    CRITICAL = "critical"
    UNKNOWN = "unknown"

class AnomalyType(Enum):
    TIMEOUT = "TIMEOUT"
    AUTH = "AUTH"
    DATABASE = "DATABASE"
    NETWORK = "NETWORK"
    VALIDATION = "VALIDATION"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    DATA_QUALITY = "DATA_QUALITY"
    UNKNOWN = "UNKNOWN"

class Priority(Enum):
    P0 = "P0"  # 立即处理
    P1 = "P1"  # 尽快处理
    P2 = "P2"  # 正常排队
    P3 = "P3"  # 低影响

class IssueStatus(Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    MITIGATED = "mitigated"
    RESOLVED = "resolved"
    IGNORED = "ignored"

@dataclass
class LogEntry:
    """标准化后的日志条目"""
    id: str
    timestamp: Optional[datetime]
    source: str
    level: LogLevel
    message: str
    job_name: Optional[str] = None
    trace_id: Optional[str] = None
    raw: dict = field(default_factory=dict)
    is_dirty: bool = False
    dirty_reason: Optional[str] = None

@dataclass
class AnomalyIssue:
    """合并后的异常问题"""
    id: str
    fingerprint: str
    anomaly_type: AnomalyType
    priority: Priority
    source: str
    message_template: str       # 去除动态内容后的消息模板
    related_log_ids: list       # 关联的原始日志ID
    first_seen: datetime
    last_seen: datetime
    occurrence_count: int
    status: IssueStatus = IssueStatus.OPEN
    needs_human: bool = False
    human_reason: Optional[str] = None
    recommendation: Optional[str] = None
    priority_reason: Optional[str] = None

@dataclass
class Stats:
    """统计信息"""
    total_logs: int = 0
    valid_logs: int = 0
    dirty_logs: int = 0
    anomaly_logs: int = 0
    merged_issues: int = 0
    by_priority: dict = field(default_factory=dict)
    by_type: dict = field(default_factory=dict)
    needs_human_count: int = 0
    auto_fixable_count: int = 0
    by_source: dict = field(default_factory=dict)
```

### ✅ 验证：运行 `python -c "from app.models import *; print('OK')"`

---

## 🧱 第四步：实现 app/parser.py

### 4.1 功能说明
读取 JSONL 文件，容错处理无效行。

### 4.2 实现思路

```python
import json
from typing import List, Tuple
from app.models import LogEntry

def parse_jsonl(file_path: str) -> Tuple[List[LogEntry], List[dict]]:
    """
    读取 JSONL 文件
    返回: (有效日志列表, 脏数据列表)
    """
    valid_logs = []
    dirty_data = []
    
    with open(file_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            
            try:
                raw = json.loads(line)
                # 先标记为有效，具体标准化在 normalizer 做
                valid_logs.append(raw)
            except json.JSONDecodeError:
                dirty_data.append({
                    "line_number": line_num,
                    "raw_content": line[:200],
                    "reason": "无效 JSON"
                })
    
    return valid_logs, dirty_data
```

### 4.3 关键边界情况
- ✅ 空行跳过不报错
- ✅ JSON 解析失败记录但不崩溃
- ✅ 编码问题用 utf-8 读（如果是 gbk 怎么处理？自己决定并记录在 DECISIONS.md）

### ✅ 验证：创建 2 条测试数据，手动运行 `parse_jsonl("data/sample_logs.jsonl")`

---

## 🧱 第五步：实现 app/normalizer.py

### 5.1 功能说明
将不同来源的异构字段映射为统一的 LogEntry。

### 5.2 字段映射规则

```python
from datetime import datetime
from typing import Optional, Any
from app.models import LogEntry, LogLevel

# 时间字段名映射（按优先级查找）
TIME_FIELDS = ["timestamp", "time", "created_at", "@timestamp", "log_time"]

# 来源字段名映射
SOURCE_FIELDS = ["source", "service", "app", "system", "application"]

# 等级字段名映射
LEVEL_FIELDS = ["level", "severity", "status", "log_level"]

# 错误信息字段名映射
MESSAGE_FIELDS = ["message", "error", "msg", "detail", "description"]

def normalize(raw: dict, line_index: int) -> LogEntry:
    """
    将原始日志字典标准化为 LogEntry
    """
    # 1. 提取各字段
    timestamp = extract_timestamp(raw)
    source = extract_source(raw)
    level = extract_level(raw)
    message = extract_message(raw)
    job_name = raw.get("job_name") or raw.get("job") or raw.get("task")
    trace_id = raw.get("trace_id") or raw.get("traceId") or raw.get("request_id")
    
    # 2. 判断是否脏数据
    is_dirty = False
    dirty_reasons = []
    
    if timestamp is None:
        is_dirty = True
        dirty_reasons.append("缺少时间戳")
    if source is None:
        dirty_reasons.append("缺少来源系统")
    if message is None or (isinstance(message, str) and len(message.strip()) < 3):
        is_dirty = True
        dirty_reasons.append("错误信息缺失或过短")
    
    # 3. 构建 LogEntry
    return LogEntry(
        id=f"log_{line_index}",
        timestamp=timestamp,
        source=source or "unknown",
        level=level,
        message=message or "(无消息)",
        job_name=job_name,
        trace_id=trace_id,
        raw=raw,
        is_dirty=is_dirty,
        dirty_reason="; ".join(dirty_reasons) if dirty_reasons else None
    )
```

### 5.3 时间解析（关键难点）

```python
def extract_timestamp(raw: dict) -> Optional[datetime]:
    """从多种时间格式中提取时间戳"""
    for field in TIME_FIELDS:
        val = raw.get(field)
        if val is None:
            continue
        
        if isinstance(val, (int, float)):
            # Unix 时间戳（秒或毫秒）
            if val > 1e12:
                val = val / 1000
            return datetime.fromtimestamp(val)
        
        if isinstance(val, str):
            # 尝试多种格式
            formats = [
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y/%m/%d %H:%M:%S",
            ]
            for fmt in formats:
                try:
                    return datetime.strptime(val, fmt)
                except ValueError:
                    continue
    return None
```

### ✅ 验证：编写不同格式的字典，测试 normalize 函数

---

## 🧱 第六步：实现 app/classifier.py

### 6.1 功能说明
判断日志是否异常，并将异常分类。

### 6.2 实现思路

```python
from app.models import LogEntry, LogLevel, AnomalyType

# 错误等级集合
ERROR_LEVELS = {LogLevel.ERROR, LogLevel.FATAL, LogLevel.CRITICAL}

# 异常关键词（中文+英文）
ANOMALY_KEYWORDS = {
    "timeout", "超时", "timed out", "timed-out",
    "exception", "异常", "error",
    "failed", "失败", "failure",
    "denied", "拒绝", "rejected",
    "unavailable", "不可用", "503",
    "connection refused", "连接被拒绝",
    "aborted", "中止",
}

def is_anomaly(log: LogEntry) -> bool:
    """判断日志是否异常"""
    # 规则1: 错误等级
    if log.level in ERROR_LEVELS:
        return True
    
    # 规则2: 关键词匹配
    msg_lower = log.message.lower()
    for kw in ANOMALY_KEYWORDS:
        if kw.lower() in msg_lower:
            return True
    
    # 规则3: 字段缺失严重
    if log.is_dirty and log.dirty_reason:
        return True
    
    return False

def classify(log: LogEntry) -> AnomalyType:
    """对异常日志进行分类"""
    msg = log.message.lower() if log.message else ""
    
    # 分类规则（按优先级排序——先匹配精确模式再泛化）
    if any(kw in msg for kw in ["timeout", "超时", "timed out", "timed-out"]):
        return AnomalyType.TIMEOUT
    
    if any(kw in msg for kw in ["auth", "认证", "token", "permission", "权限", "unauthorized", "未授权", "denied", "forbidden"]):
        return AnomalyType.AUTH
    
    if any(kw in msg for kw in ["database", "数据库", "db", "sql", "mysql", "postgres", "mongo", "redis", "连接池", "connection pool"]):
        return AnomalyType.DATABASE
    
    if any(kw in msg for kw in ["network", "网络", "connection refused", "连接失败", "dns", "tcp", "socket", "host", "端口"]):
        return AnomalyType.NETWORK
    
    if any(kw in msg for kw in ["validation", "校验", "invalid", "无效", "param", "参数", "required", "必填", "format", "格式"]):
        return AnomalyType.VALIDATION
    
    if any(kw in msg for kw in ["limit", "限流", "throttle", "quota", "容量", "overload", "过载", "queue", "积压", "full", "已满"]):
        return AnomalyType.RESOURCE_LIMIT
    
    if log.is_dirty:
        return AnomalyType.DATA_QUALITY
    
    return AnomalyType.UNKNOWN
```

### 6.3 分类优先级规则
关键词匹配有先后顺序，先匹配到的优先。比如一条日志同时含 "timeout" 和 "database"，归为 TIMEOUT。**这个决策要在 DECISIONS.md 中解释。**

### ✅ 验证：准备几条不同分类的日志，逐个测试 classify

---

## 🧱 第七步：实现 app/deduplicator.py（⭐ 核心模块）

### 7.1 功能说明
生成异常指纹（fingerprint），合并同一根因的异常日志。

### 7.2 关键：动态内容去除

```python
import re
import hashlib

# 需要去除的动态模式
DYNAMIC_PATTERNS = [
    # UUID（各种格式）
    (re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I), '<UUID>'),
    # 日期时间
    (re.compile(r'\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?'), '<DATETIME>'),
    # IP 地址
    (re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b'), '<IP>'),
    # 订单号（常见格式）
    (re.compile(r'order[_-]?\d+', re.I), '<ORDER_ID>'),
    # 用户ID
    (re.compile(r'user[_-]?\d+', re.I), '<USER_ID>'),
    # 纯数字ID（长数字）
    (re.compile(r'\b\d{6,}\b'), '<NUM_ID>'),
    # trace_id 后的值
    (re.compile(r'trace[_-]?id[=:]\s*\S+', re.I), 'trace_id=<TRACE_ID>'),
    # 金额
    (re.compile(r'\b\d+\.?\d*\s*(?:元|USD|CNY|dollars?)\b', re.I), '<AMOUNT>'),
    # 路径中的数字
    (re.compile(r'/\d+/'), '/<ID>/'),
]

SYNONYM_PATTERNS = [
    (re.compile(r'\b(?:timeout|timed out|timed-out)\b|超时', re.I), 'TIMEOUT'),
    (re.compile(r'\b(?:database|db|mysql|postgres|redis)\b|数据库', re.I), 'DATABASE'),
    (re.compile(r'\b(?:failed|failure)\b|失败', re.I), 'FAILED'),
    (re.compile(r'\b(?:permission denied|unauthorized|forbidden|auth)\b|无权限|未授权|认证', re.I), 'AUTH'),
    (re.compile(r'\b(?:connection refused|network|dns|socket)\b|连接被拒绝|网络', re.I), 'NETWORK'),
    (re.compile(r'\b(?:rate limit|throttle|quota|limit)\b|限流|配额', re.I), 'RESOURCE_LIMIT'),
]

def normalize_message(message: str) -> str:
    """去除动态内容并归一化同义词，生成标准化模板"""
    result = message
    for pattern, replacement in DYNAMIC_PATTERNS:
        result = pattern.sub(replacement, result)
    for pattern, replacement in SYNONYM_PATTERNS:
        result = pattern.sub(replacement, result)
    result = re.sub(r'\s+', ' ', result).strip().lower()
    return result

def generate_fingerprint(log: LogEntry, anomaly_type: AnomalyType) -> str:
    """
    生成异常指纹
    
    指纹组成：来源系统 + 异常类型 + 标准化消息模板
    这样同来源、同类型、同根因的异常会生成相同指纹
    """
    normalized_msg = normalize_message(log.message)
    
    # 组合指纹内容
    fingerprint_content = f"{log.source}|{anomaly_type.value}|{normalized_msg}"
    
    return hashlib.md5(fingerprint_content.encode()).hexdigest()
```

### 7.3 合并逻辑

```python
from collections import defaultdict
from typing import List
from app.models import LogEntry, AnomalyIssue, AnomalyType

def merge_anomalies(anomaly_logs: List[LogEntry], classified: dict) -> List[AnomalyIssue]:
    """
    将异常日志按 fingerprint 合并
    """
    # 按 fingerprint 分组
    groups = defaultdict(list)
    for log in anomaly_logs:
        fp = generate_fingerprint(log, classified[log.id])
        groups[fp].append(log)
    
    # 为每组生成一个 AnomalyIssue
    issues = []
    for i, (fp, logs) in enumerate(groups.items()):
        sorted_logs = sorted(logs, key=lambda l: l.timestamp or datetime.min)
        
        # 确定异常类型（取该组出现最多的类型）
        type_counts = defaultdict(int)
        for log in logs:
            type_counts[classified[log.id]] += 1
        dominant_type = max(type_counts, key=type_counts.get)
        
        issues.append(AnomalyIssue(
            id=f"issue_{i}",
            fingerprint=fp,
            anomaly_type=dominant_type,
            priority=None,  # 稍后由 prioritizer 填充
            source=logs[0].source,
            message_template=normalize_message(logs[0].message),
            related_log_ids=[log.id for log in logs],
            first_seen=sorted_logs[0].timestamp or datetime.now(),
            last_seen=sorted_logs[-1].timestamp or datetime.now(),
            occurrence_count=len(logs),
        ))
    
    return issues
```

### ✅ 验证测试重点
- ✅ 同一错误含不同订单号 → 同一个 fingerprint
- ✅ 中英文同义词 → 同一个 fingerprint（需要 classifier 先归一化类型）
- ✅ 不同根因相似文案 → 不同 fingerprint
- ⚠️ 关于中英文同义词归一化：这里 fingerprint 依赖 anomaly_type（经 classifier 归一化），所以"timeout"和"超时"都会先被 classifier 归为 TIMEOUT，再进入 fingerprint 计算。

---

## 🧱 第八步：实现 app/prioritizer.py（⭐ 核心模块）

### 8.1 功能说明
基于多因素评分为每个异常问题生成优先级。

### 8.2 评分算法

```python
from app.models import AnomalyIssue, AnomalyType, Priority

# 核心服务定义
CORE_SERVICES = {"payment-service", "order-worker", "auth-gateway", "risk-api"}

# 权重配置
WEIGHTS = {
    "severity": 0.30,     # 严重程度
    "frequency": 0.25,    # 重复次数
    "core_service": 0.20, # 核心业务链路
    "unknown": 0.15,      # 未知错误
    "data_quality": 0.10, # 数据质量
}

def calculate_priority(issue: AnomalyIssue) -> tuple:
    """
    计算优先级分数并返回 (Priority, score, reason)
    """
    score = 0.0
    reasons = []
    
    # 1. 严重程度 (0-10 分)
    severity_scores = {
        AnomalyType.TIMEOUT: 5,
        AnomalyType.AUTH: 7,          # 认证问题可能涉及安全
        AnomalyType.DATABASE: 8,      # 数据库通常是核心依赖
        AnomalyType.NETWORK: 4,
        AnomalyType.VALIDATION: 2,
        AnomalyType.RESOURCE_LIMIT: 3,
        AnomalyType.DATA_QUALITY: 1,
        AnomalyType.UNKNOWN: 9,       # 未知问题需要最高警惕
    }
    severity = severity_scores.get(issue.anomaly_type, 5)
    score += WEIGHTS["severity"] * severity
    reasons.append(f"严重程度={severity}({issue.anomaly_type.value})")
    
    # 2. 重复次数 (对数缩放，0-10 分)
    import math
    frequency = min(10, math.log2(1 + issue.occurrence_count) * 2.5)
    score += WEIGHTS["frequency"] * frequency
    reasons.append(f"重复次数={issue.occurrence_count}(得分{frequency:.1f})")
    
    # 3. 核心业务链路 (0 或 10 分)
    is_core = any(svc in issue.source for svc in CORE_SERVICES)
    core_score = 10 if is_core else 0
    score += WEIGHTS["core_service"] * core_score
    reasons.append(f"核心链路={'是' if is_core else '否'}(得分{core_score})")
    
    # 4. 未知错误 (0 或 10 分)
    is_unknown = issue.anomaly_type == AnomalyType.UNKNOWN
    unknown_score = 10 if is_unknown else 0
    score += WEIGHTS["unknown"] * unknown_score
    reasons.append(f"未知错误={'是' if is_unknown else '否'}(得分{unknown_score})")
    
    # 5. 数据质量 (0 或 10 分)
    dq_score = 10 if issue.anomaly_type == AnomalyType.DATA_QUALITY else 0
    score += WEIGHTS["data_quality"] * dq_score
    
    # 6. 时间连续性加分
    if issue.first_seen and issue.last_seen:
        time_span = (issue.last_seen - issue.first_seen).total_seconds()
        if time_span > 3600:  # 持续超过1小时
            score += 1.0
            reasons.append("持续发生超1小时(+1.0)")
    
    # 分数映射到优先级
    # 注意：前面的加权分数已经是 0-10 量级，不要再除以 10，
    # 否则 P0/P1/P2 阈值会失效。
    if score >= 7:
        priority = Priority.P0
    elif score >= 4:
        priority = Priority.P1
    elif score >= 2:
        priority = Priority.P2
    else:
        priority = Priority.P3
    
    return priority, round(score, 1), "; ".join(reasons)
```

### 8.3 优先级阈值

| 分数范围 | 优先级 | 含义 |
|---------|--------|------|
| 7.0+ | P0 | 立即处理 |
| 4.0-6.9 | P1 | 尽快处理 |
| 2.0-3.9 | P2 | 正常排队 |
| 0-1.9 | P3 | 低影响 |

### ✅ 验证
- ✅ 重复次数增加 → 优先级升高
- ✅ 核心服务异常 → 优先级更高
- ✅ UNKNOWN 类型 → 默认高分

---

## 🧱 第九步：实现 app/recommender.py

### 9.1 功能说明
生成处理建议，判断是否需要人工介入。

### 9.2 实现代码

```python
from app.models import AnomalyIssue, AnomalyType, Priority

RECOMMENDATIONS = {
    AnomalyType.TIMEOUT: "检查下游服务可用性、网络延迟和重试配置。确认超时阈值是否合理。",
    AnomalyType.AUTH: "检查 token 有效性、权限配置和凭证是否过期。排查认证服务日志。",
    AnomalyType.DATABASE: "检查数据库连接池状态、慢查询日志、锁等待和实例健康状态。",
    AnomalyType.NETWORK: "检查网络连通性、DNS 解析、防火墙规则和服务发现配置。",
    AnomalyType.VALIDATION: "检查输入数据格式、必填字段完整性和上游数据质量。",
    AnomalyType.RESOURCE_LIMIT: "检查限流配置、队列积压情况、容量水位和扩容策略。",
    AnomalyType.DATA_QUALITY: "检查日志采集管道，确认字段映射规则和数据格式规范。",
    AnomalyType.UNKNOWN: "需要人工查看原始日志和上下文信息，确定根因后补充分类规则。",
}

def get_recommendation(issue: AnomalyIssue) -> str:
    """获取处理建议"""
    return RECOMMENDATIONS.get(issue.anomaly_type, "需要进一步分析")

def need_human(issue: AnomalyIssue) -> tuple:
    """
    判断是否需要人工介入
    
    返回: (是否需要人工, 原因)
    """
    reasons = []
    
    # 规则1: UNKNOWN 类型必须人工
    if issue.anomaly_type == AnomalyType.UNKNOWN:
        reasons.append("未知类型异常，需人工确认根因")
    
    # 规则2: P0/P1 需要人工
    if issue.priority in (Priority.P0, Priority.P1):
        reasons.append(f"{issue.priority.value} 高优先级问题")
    
    # 规则3: 重复次数超过阈值
    if issue.occurrence_count > 5:
        reasons.append(f"重复发生 {issue.occurrence_count} 次，超过自动处理阈值")
    
    # 规则4: 核心业务链路
    CORE_SERVICES = {"payment-service", "order-worker", "auth-gateway"}
    if any(svc in issue.source for svc in CORE_SERVICES):
        if issue.priority in (Priority.P0, Priority.P1):
            reasons.append(f"涉及核心服务 {issue.source}")
    
    # 规则5: 涉及安全/权限/资金
    if issue.anomaly_type == AnomalyType.AUTH:
        reasons.append("涉及认证安全，需人工确认")
    
    # 自动处理判断
    can_auto = []
    
    if issue.anomaly_type == AnomalyType.TIMEOUT and issue.occurrence_count <= 2:
        can_auto.append("低频超时，可自动重试")
    
    if issue.priority == Priority.P3:
        can_auto.append("低优先级，可自动处理")
    
    if issue.anomaly_type == AnomalyType.NETWORK and issue.occurrence_count <= 2:
        can_auto.append("低频网络抖动，可自动恢复")
    
    # 综合判断：有 reasons 就需要人工，除非 can_auto 明确覆盖
    if can_auto and not reasons:
        return False, "可自动处理: " + "; ".join(can_auto)
    
    if reasons:
        return True, "; ".join(reasons)
    
    return False, "低影响问题，可自动处理"
```

### ✅ 验证
- ✅ UNKNOWN 类型 → needs_human=True
- ✅ P3 + 低频 → needs_human=False
- ✅ P0 → needs_human=True

---

## 🧱 第十步：实现 app/storage.py

### 10.1 功能说明
使用 SQLite 存储解析结果和异常问题。

### 10.2 实现代码

```python
import sqlite3
import json
from typing import List, Optional
from app.models import LogEntry, AnomalyIssue, IssueStatus, LogLevel, AnomalyType, Priority

DB_PATH = "anomaly_triage.db"

def init_db():
    """初始化数据库"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id TEXT PRIMARY KEY,
            timestamp TEXT,
            source TEXT,
            level TEXT,
            message TEXT,
            job_name TEXT,
            trace_id TEXT,
            raw TEXT,
            is_dirty INTEGER,
            dirty_reason TEXT
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS issues (
            id TEXT PRIMARY KEY,
            fingerprint TEXT,
            anomaly_type TEXT,
            priority TEXT,
            source TEXT,
            message_template TEXT,
            related_log_ids TEXT,
            first_seen TEXT,
            last_seen TEXT,
            occurrence_count INTEGER,
            status TEXT DEFAULT 'open',
            needs_human INTEGER DEFAULT 0,
            human_reason TEXT,
            recommendation TEXT,
            priority_reason TEXT
        )
    """)
    
    conn.commit()
    conn.close()

def save_logs(logs: List[LogEntry]):
    """保存标准化日志"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for log in logs:
        c.execute("INSERT OR REPLACE INTO logs VALUES (?,?,?,?,?,?,?,?,?,?)", (
            log.id, str(log.timestamp), log.source, log.level.value,
            log.message, log.job_name, log.trace_id,
            json.dumps(log.raw, ensure_ascii=False),
            int(log.is_dirty), log.dirty_reason
        ))
    conn.commit()
    conn.close()

def save_issues(issues: List[AnomalyIssue]):
    """保存异常问题"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for issue in issues:
        c.execute("INSERT OR REPLACE INTO issues VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            issue.id, issue.fingerprint, issue.anomaly_type.value,
            issue.priority.value if issue.priority else None,
            issue.source, issue.message_template,
            json.dumps(issue.related_log_ids),
            str(issue.first_seen), str(issue.last_seen),
            issue.occurrence_count, issue.status.value,
            int(issue.needs_human), issue.human_reason,
            issue.recommendation, issue.priority_reason
        ))
    conn.commit()
    conn.close()

def update_issue_status(issue_id: str, new_status: IssueStatus):
    """更新异常状态"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE issues SET status = ? WHERE id = ?", (new_status.value, issue_id))
    conn.commit()
    conn.close()

def get_all_issues() -> List[dict]:
    """获取所有异常问题"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM issues ORDER BY priority, occurrence_count DESC")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_stats() -> dict:
    """获取统计信息"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    total = c.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
    valid = c.execute("SELECT COUNT(*) FROM logs WHERE is_dirty = 0").fetchone()[0]
    dirty = c.execute("SELECT COUNT(*) FROM logs WHERE is_dirty = 1").fetchone()[0]
    anomaly = c.execute("SELECT COUNT(*) FROM logs WHERE level IN ('error','fatal','critical')").fetchone()[0]
    merged = c.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
    
    by_priority = {}
    for row in c.execute("SELECT priority, COUNT(*) FROM issues GROUP BY priority"):
        by_priority[row[0]] = row[1]
    
    by_type = {}
    for row in c.execute("SELECT anomaly_type, COUNT(*) FROM issues GROUP BY anomaly_type"):
        by_type[row[0]] = row[1]
    
    human = c.execute("SELECT COUNT(*) FROM issues WHERE needs_human = 1").fetchone()[0]
    auto = merged - human
    
    by_source = {}
    for row in c.execute("SELECT source, COUNT(*) FROM issues GROUP BY source"):
        by_source[row[0]] = row[1]
    
    conn.close()
    
    return {
        "total_logs": total,
        "valid_logs": valid,
        "dirty_logs": dirty,
        "anomaly_logs": anomaly,
        "merged_issues": merged,
        "by_priority": by_priority,
        "by_type": by_type,
        "needs_human_count": human,
        "auto_fixable_count": auto,
        "by_source": by_source,
    }
```

---

## 🧱 第十一步：实现 app/main.py（FastAPI 入口）

### 11.1 核心路由

```python
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from app.parser import parse_jsonl
from app.normalizer import normalize
from app.classifier import is_anomaly, classify
from app.deduplicator import merge_anomalies
from app.prioritizer import calculate_priority
from app.recommender import get_recommendation, need_human
from app.storage import init_db, save_logs, save_issues, get_all_issues, get_stats, update_issue_status
from app.models import IssueStatus

app = FastAPI(title="日志异常分诊工具")

@app.on_event("startup")
def startup():
    init_db()
    # 启动时自动加载和处理数据
    process_logs("data/sample_logs.jsonl")

def process_logs(file_path: str):
    """完整的处理流水线"""
    # 1. 解析
    raw_logs, dirty = parse_jsonl(file_path)
    # 注意：dirty 是 parse 阶段的无效 JSON 行，后续统计不能丢。
    # 实现时可以将它保存为特殊 dirty log，或把 len(dirty) 传入统计函数。
    
    # 2. 标准化
    entries = [normalize(raw, i) for i, raw in enumerate(raw_logs)]
    
    # 3. 保存全部日志
    save_logs(entries)
    
    # 4. 筛选异常
    anomalies = [e for e in entries if is_anomaly(e)]
    
    # 5. 分类
    classified = {e.id: classify(e) for e in anomalies}
    
    # 6. 合并
    issues = merge_anomalies(anomalies, classified)
    
    # 7. 优先级 + 建议 + 人工判断
    for issue in issues:
        priority, score, reason = calculate_priority(issue)
        issue.priority = priority
        issue.priority_reason = reason
        issue.recommendation = get_recommendation(issue)
        nh, nh_reason = need_human(issue)
        issue.needs_human = nh
        issue.human_reason = nh_reason
    
    # 8. 保存
    save_issues(issues)

@app.get("/api/issues")
def list_issues():
    """异常列表 API"""
    return {"issues": get_all_issues()}

@app.get("/api/issues/{issue_id}")
def get_issue(issue_id: str):
    """异常详情 API"""
    issues = get_all_issues()
    for issue in issues:
        if issue["id"] == issue_id:
            return {"issue": issue}
    raise HTTPException(status_code=404, detail="issue not found")

@app.put("/api/issues/{issue_id}/status")
def change_status(issue_id: str, status: str = Query(...)):
    """更新状态 API"""
    try:
        new_status = IssueStatus(status)
        update_issue_status(issue_id, new_status)
        return {"ok": True}
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效状态: {status}")

@app.get("/api/stats")
def statistics():
    """统计 API"""
    return get_stats()

@app.get("/", response_class=HTMLResponse)
def index():
    """前端页面"""
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()
```

### 11.2 启动命令

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

访问 http://localhost:8000 查看前端页面。

---

## 📊 第十二步：构造模拟数据 data/sample_logs.jsonl

### 12.1 数据设计原则
必须覆盖以下所有场景（目标 50 条）：

| 场景 | 数量 |
|------|------|
| 不同时间格式 | 贯穿全部 |
| 同一任务重复失败 | 6-8 条 |
| 同一根因不同表述 | 8-10 条 |
| 中英文同义词 | 10+ 条 |
| 包含动态 ID/订单号 | 15+ 条 |
| 缺少关键字段 | 3-5 条 |
| 无效 JSON | 2-3 条 |
| 正常日志 | 5-10 条 |
| 时间乱序 | 随机 |

### 12.2 示例日志（按来源系统分类）

**payment-service（支付服务）：**
```jsonl
{"service":"payment-service","level":"error","timestamp":"2024-03-15T10:23:45Z","message":"Database timeout after 30000ms for order_id=ORD-88271, connection pool exhausted","trace_id":"a1b2c3d4-e5f6-7890-abcd-ef1234567890","job_name":"process_payment"}
{"service":"payment-service","level":"error","timestamp":"2024-03-15T10:24:12Z","message":"数据库超时: order_id=ORD-99231, 连接池已满","trace_id":"b2c3d4e5-f6a7-8901-bcde-f12345678901","job_name":"process_payment"}
{"service":"payment-service","level":"error","timestamp":"2024-03-15T10:25:33Z","message":"db connection timeout for order_id=ORD-10342","job_name":"process_payment"}
{"service":"payment-service","level":"info","time":"2024-03-15T10:20:00Z","msg":"Payment processed OK: order_id=ORD-88100"}
```

**order-worker（订单服务）：**
```jsonl
{"source":"order-worker","severity":"error","created_at":1710498225,"error":"Failed to update inventory: connection refused to inventory-service:9090","trace_id":"x1y2z3-abc-def"}
{"source":"order-worker","severity":"error","created_at":1710498350,"error":"无法连接库存服务: dial tcp 10.0.1.50:9090: connection refused","trace_id":"x1y2z3-def-ghi"}
{"source":"order-worker","severity":"fatal","created_at":1710499000,"error":"database is unavailable after 3 retries for transaction TX-9942, order_id=ORD-12345 cannot be committed"}
```

**auth-gateway（认证网关）：**
```jsonl
{"app":"auth-gateway","level":"error","time":"2024/03/15 12:05:30","msg":"token validation failed for user_id=55432: JWT signature invalid","request_id":"req-abc-123"}
{"app":"auth-gateway","level":"error","time":"2024/03/15 12:06:00","msg":"用户认证失败: permission denied for user_id=66123 accessing /api/admin"}
{"app":"auth-gateway","level":"warn","@timestamp":"2024-03-15T12:10:00Z","detail":"rate limit exceeded for IP 192.168.1.100, 503 returned"}
```

**inventory-service（库存服务）：**
```jsonl
{"system":"inventory-service","level":"error","log_time":"2024-03-15 11:30:00","message":"inventory validation failed: SKU=INVALID_FORAMT, required field 'quantity' missing","job":"sync_inventory"}
{"system":"inventory-service","level":"error","log_time":"2024-03-15 11:35:00","message":"Redis 连接超时，缓存查询失败: getStockLevel(user_id=44556, product_id=998877)"}
```

**etl-job（数据任务）：**
```jsonl
{"source":"etl-job","level":"error","timestamp":"2024-03-14T23:00:00Z","msg":"ETL job daily_sync failed: source database timed out reading batch 445, retry 1/3","task":"daily_sync"}
{"source":"etl-job","level":"error","timestamp":"2024-03-14T23:05:00Z","msg":"ETL job daily_sync failed: source database timed out reading batch 446, retry 2/3","task":"daily_sync"}
{"source":"etl-job","level":"error","timestamp":"2024-03-14T23:10:00Z","msg":"ETL job daily_sync failed after all retries: database connection lost","task":"daily_sync"}
```

**脏数据示例：**
```jsonl
{"service":"unknown-worker","level":"info","timestamp":"2024-03-15T09:00:00Z","message":"运行中"}
这是完全无效的内容不是JSON
{"timestamp":"2024-03-15T10:00:00Z","source":"bad-worker"}  
{"source":"bad-worker","level":"error","timestamp":"INVALID_DATE"}
{"system":"broken-service","severity":"error","created_at":1710499000}
```

### ✅ 建议构造 50 条日志，覆盖以下组合：
- payment-service: 8-10 条（含正常和异常）
- order-worker: 8-10 条
- auth-gateway: 6-8 条
- inventory-service: 5-7 条
- notification-worker: 4-6 条
- etl-job: 5-7 条
- 未知来源/其他: 3-5 条
- 脏数据（无效JSON）: 2-3 条

---

## 🧪 第十三步：编写测试

### 13.1 test_parser.py

```python
import json
import pytest
import os
import tempfile
from app.parser import parse_jsonl

def test_parse_valid_jsonl():
    """测试读取合法 JSONL"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False, encoding='utf-8') as f:
        f.write('{"level":"error","message":"test1"}\n')
        f.write('{"level":"info","message":"test2"}\n')
        f.close()
        
        logs, dirty = parse_jsonl(f.name)
        assert len(logs) == 2
        assert len(dirty) == 0
    
    os.unlink(f.name)

def test_parse_invalid_json_not_crash():
    """无效 JSON 不导致崩溃"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False, encoding='utf-8') as f:
        f.write('{"level":"error","message":"ok"}\n')
        f.write('这不是JSON\n')
        f.write('{"level":"info","message":"ok2"}\n')
        f.close()
        
        logs, dirty = parse_jsonl(f.name)
        assert len(logs) == 2
        assert len(dirty) == 1
        assert dirty[0]["reason"] == "无效 JSON"
    
    os.unlink(f.name)

def test_parse_empty_file():
    """空文件处理"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        f.close()
        logs, dirty = parse_jsonl(f.name)
        assert len(logs) == 0
        assert len(dirty) == 0
    
    os.unlink(f.name)
```

### 13.2 test_normalizer.py

```python
from app.normalizer import normalize

def test_normalize_different_field_names():
    """测试不同字段名标准化"""
    # 测试各种时间字段
    log_with_timestamp = {"timestamp": "2024-03-15T10:00:00Z", "service": "test", "level": "error", "message": "test"}
    entry = normalize(log_with_timestamp, 0)
    assert entry.source == "test"
    assert entry.timestamp is not None
    
    log_with_time = {"time": 1710498225, "app": "test2", "level": "info", "message": "test"}
    entry2 = normalize(log_with_time, 1)
    assert entry2.source == "test2"
    assert entry2.timestamp is not None

def test_missing_fields_no_crash():
    """缺少字段不崩溃"""
    log = {"level": "error"}  # 缺很多字段
    entry = normalize(log, 0)
    assert entry.source == "unknown"
    assert entry.level.value == "error"
    assert entry.message == "(无消息)"

def test_dirty_data_marked():
    """脏数据被正确标记"""
    log = {}  # 全空的
    entry = normalize(log, 0)
    assert entry.is_dirty
    assert "缺少时间戳" in entry.dirty_reason
```

### 13.3 test_classifier.py

```python
from app.classifier import is_anomaly, classify
from app.models import LogEntry, LogLevel, AnomalyType
from datetime import datetime

def make_log(message, level=LogLevel.ERROR, source="test"):
    """辅助函数"""
    return LogEntry(
        id="x", timestamp=datetime.now(), source=source,
        level=level, message=message
    )

def test_is_anomaly_error_level():
    assert is_anomaly(make_log("something", level=LogLevel.ERROR))
    assert is_anomaly(make_log("fatal", level=LogLevel.FATAL))

def test_is_anomaly_keyword():
    assert is_anomaly(make_log("request timeout", level=LogLevel.WARN))
    assert is_anomaly(make_log("请求超时", level=LogLevel.INFO))

def test_normal_not_anomaly():
    assert not is_anomaly(make_log("all systems operational", level=LogLevel.INFO))

def test_classify_timeout_both_languages():
    """中英文 timeout 都归为 TIMEOUT"""
    log_en = make_log("database timeout after 30s", level=LogLevel.ERROR)
    assert classify(log_en) == AnomalyType.TIMEOUT
    
    log_cn = make_log("数据库超时", level=LogLevel.ERROR)
    assert classify(log_cn) == AnomalyType.TIMEOUT

def test_classify_unknown():
    """未知错误消息归为 UNKNOWN"""
    log = make_log("something strange happened", level=LogLevel.ERROR)
    assert classify(log) == AnomalyType.UNKNOWN
```

### 13.4 test_deduplicator.py（⭐ 最重要）

```python
from app.deduplicator import normalize_message, generate_fingerprint
from app.models import LogEntry, LogLevel, AnomalyType
from datetime import datetime

def make_log(message, source="test-service"):
    return LogEntry(
        id="x", timestamp=datetime.now(), source=source,
        level=LogLevel.ERROR, message=message, trace_id="tr-123"
    )

def test_normalize_removes_order_id():
    msg = "timeout for order_id=ORD-88271"
    normalized = normalize_message(msg)
    assert "ORD-88271" not in normalized
    assert "<ORDER_ID>" in normalized

def test_normalize_removes_uuid():
    msg = "error at a1b2c3d4-e5f6-7890-abcd-ef1234567890 while processing"
    normalized = normalize_message(msg)
    assert "a1b2c3d4" not in normalized
    assert "<UUID>" in normalized

def test_same_error_different_ids_same_fingerprint():
    """同一错误含不同ID → 同一 fingerprint"""
    log1 = make_log("timeout for order_id=ORD-88271")
    log2 = make_log("timeout for order_id=ORD-99231")
    
    fp1 = generate_fingerprint(log1, AnomalyType.TIMEOUT)
    fp2 = generate_fingerprint(log2, AnomalyType.TIMEOUT)
    
    assert fp1 == fp2  # ⭐ 关键断言

def test_different_root_cause_different_fingerprint():
    """不同根因相似文案 → 不同 fingerprint"""
    log1 = make_log("database timeout")  # TIMEOUT
    log2 = make_log("permission denied")  # AUTH
    
    fp1 = generate_fingerprint(log1, AnomalyType.TIMEOUT)
    fp2 = generate_fingerprint(log2, AnomalyType.AUTH)
    
    assert fp1 != fp2  # ⭐ 不同根因不能合并

def test_chinese_english_same_type_same_fingerprint():
    """中英文同类错误 → 同一 fingerprint"""
    log1 = make_log("database timeout 超时")
    log2 = make_log("数据库超时 db timeout")
    
    # 经 classifier 都归为 TIMEOUT
    fp1 = generate_fingerprint(log1, AnomalyType.TIMEOUT)
    fp2 = generate_fingerprint(log2, AnomalyType.TIMEOUT)
    
    # 标准化后中英文同义词应归一到同一模板
    assert fp1 == fp2
```

### 13.5 test_prioritizer.py（⭐ 重要）

```python
from app.prioritizer import calculate_priority
from app.models import AnomalyIssue, AnomalyType, Priority
from datetime import datetime

def make_issue(anomaly_type, source, count, first=None, last=None):
    return AnomalyIssue(
        id="x", fingerprint="fp", anomaly_type=anomaly_type,
        priority=None, source=source, message_template="test",
        related_log_ids=[], first_seen=first or datetime.now(),
        last_seen=last or datetime.now(), occurrence_count=count
    )

def test_higher_frequency_higher_priority():
    """重复次数越多优先级越高"""
    issue_low = make_issue(AnomalyType.TIMEOUT, "test-worker", count=1)
    issue_high = make_issue(AnomalyType.TIMEOUT, "test-worker", count=20)
    
    p_low, _, _ = calculate_priority(issue_low)
    p_high, _, _ = calculate_priority(issue_high)
    
    # 20次重复应该不低于1次重复
    assert p_high.value <= p_low.value  # P0 < P1 < P2 < P3

def test_core_service_higher_priority():
    """核心服务优先级更高"""
    non_core = make_issue(AnomalyType.TIMEOUT, "test-worker", count=3)
    core = make_issue(AnomalyType.TIMEOUT, "payment-service", count=3)
    
    _, score_nc, _ = calculate_priority(non_core)
    _, score_c, _ = calculate_priority(core)
    
    assert score_c > score_nc

def test_unknown_needs_higher_score():
    """UNKNOWN 类型得分更高"""
    timeout = make_issue(AnomalyType.TIMEOUT, "test-worker", count=1)
    unknown = make_issue(AnomalyType.UNKNOWN, "test-worker", count=1)
    
    _, score_t, _ = calculate_priority(timeout)
    _, score_u, _ = calculate_priority(unknown)
    
    assert score_u > score_t
```

### 13.6 test_recommender.py

```python
from app.recommender import get_recommendation, need_human
from app.models import AnomalyIssue, AnomalyType, Priority
from datetime import datetime

def make_issue(anomaly_type, source, count, priority=Priority.P2):
    return AnomalyIssue(
        id="x", fingerprint="fp", anomaly_type=anomaly_type,
        priority=priority, source=source, message_template="test",
        related_log_ids=[], first_seen=datetime.now(),
        last_seen=datetime.now(), occurrence_count=count
    )

def test_unknown_needs_human():
    """UNKNOWN 类型必须人工"""
    issue = make_issue(AnomalyType.UNKNOWN, "test-worker", count=1, priority=Priority.P3)
    nh, reason = need_human(issue)
    assert nh is True

def test_low_freq_timeout_can_auto():
    """低频 timeout 可自动处理"""
    issue = make_issue(AnomalyType.TIMEOUT, "test-worker", count=1, priority=Priority.P3)
    nh, reason = need_human(issue)
    assert nh is False

def test_p0_needs_human():
    """P0 必须人工"""
    issue = make_issue(AnomalyType.TIMEOUT, "test-worker", count=1, priority=Priority.P0)
    nh, reason = need_human(issue)
    assert nh is True
```

### 运行测试：`pytest tests/ -v`

---

## 🖥️ 第十四步：创建前端页面 static/index.html

### 14.1 简单设计

一个 HTML 页面，通过 JavaScript fetch API 调用后端：
- 顶部：统计卡片（总日志数、异常数、合并问题数等）
- 中间：异常问题列表（表格，按优先级排序）
- 底部：每个问题的详情展开
- 状态更新按钮

### 14.2 关键要求
- ✅ 功能真实可用（调用真实 API）
- ✅ 状态可以更新
- ✅ 列表可以展开看详情
- ❌ 不要花太多时间在 UI 美化上

（具体 HTML 代码略，使用 AI 生成即可，但必须确保所有按钮都真实可用）

---

## 📝 第十五步：编写 README.md

README.md 按照以下大纲写（用 AI 辅助生成，但关键决策必须自己写）：

```markdown
# 日志异常分诊工具

## 项目简介
一句话说明工具做什么

## 快速开始
### 1. 安装依赖
### 2. 启动项目
### 3. 访问页面

## 运行测试
如何运行 pytest

## 设计说明
### 如何定义"同一个异常"
（你自己写：指纹算法 = 来源 + 类型 + 标准化消息模板）

### 如何做异常分类
（你自己写：关键词规则匹配 + 分类优先级）

### 如何判断优先级
（你自己写：多因素加权评分公式）

### 如何判断是否需要人工介入
（你自己写：UNKNOWN 默认人工，P0/P1 需要人工，核心业务需要人工...）

### 如何处理脏数据
（你自己写：记录但不崩溃，标记原因）

## 技术选型
为什么选 Python + FastAPI + SQLite + pytest

## 线上化扩展
如果这是线上系统，还需要补什么
```

---

## 📝 第十六步：编写 AI_USAGE.md（⭐ 最关键文档之一）

### 16.1 必须诚实记录的内容

```markdown
# AI 使用说明

## 使用的 AI 工具
- Claude Code (Anthropic Opus 4.8)

## AI 协助理解需求
- 如何让 AI 帮你分析招聘题面
- AI 帮你识别了哪些评估重点

## AI 协助设计方案
- AI 帮你做了模块划分
- 你接受了什么，调整了什么

## AI 协助生成/修改代码
- 哪些模块完全由你编写
- 哪些模块借助 AI 生成
- AI 生成后你修改了什么

## AI 协助设计测试
- 测试设计思路是否来自 AI
- 你自己补充了哪些边界测试

## AI 的错误或不靠谱建议 ⭐⭐⭐
（这个部分最重要！至少写 2-3 个）

### 错误 1: AI 最初建议用 simple message hash 做合并
- **AI 说了什么:** ...
- **问题是什么:** ...
- **你如何发现:** ...
- **你如何修正:** ...

### 错误 2: AI 建议直接用 level 字段映射优先级
- **AI 说了什么:** ...
- **问题是什么:** ...
- **你如何发现:** ...
- **你如何修正:** ...

### 错误 3: AI 建议把数据直接写死在 HTML 里
- **AI 说了什么:** ...
- **问题是什么:** ...
- **你如何发现:** ...
- **你如何修正:** ...

## 你自己做的关键决策 ⭐⭐⭐
（不能都是 AI 做的！）

- 异常合并规则中的 fingerprint 算法是我自己设计的
- 优先级评分的权重分配是我自己确定的
- 人工介入的 6 条判断规则是我自己定义的
- 脏数据的处理策略是我自己决定的
- ...
```

---

## 📝 第十七步：编写 DECISIONS.md（⭐ 最关键文档之二）

```markdown
# 设计决策说明

## 对需求的假设
- 核心业务链路包括: payment-service, order-worker, auth-gateway, risk-api
- 数据是准实时的，但可能有时间乱序
- 用户是技术人员，不需要复杂的 UI
- ...

## 放弃的方案

### 放弃方案 1: 用机器学习做异常聚类
- **原因:** 不可解释，需要训练数据，黑盒决策
- **选择方案:** 基于规则的 fingerprint 合并

### 放弃方案 2: 用 Elasticsearch 做日志存储
- **原因:** 太重，部署复杂，不符合 MVP 定位
- **选择方案:** SQLite + JSONL

### 放弃方案 3: React/Vue 做前端
- **原因:** 过度设计，时间有限
- **选择方案:** 原生 HTML + Fetch API

## 异常合并规则
（详细描述 fingerprint 算法和归一化策略）

## 优先级规则
（详细描述评分公式、权重、阈值）

## AI vs 自己决定
| 决策 | 谁做的 |
|------|--------|
| 技术栈选择 | AI 建议，我确认 |
| Fingerprint 算法 | 我自己设计 |
| 优先级权重 | 我自己定义 |
| 分类关键词 | AI 生成，我补充中文关键词 |
| ... | ... |

## 错误修正机制
如果分类或优先级判断错了，系统如何被发现和修正：
1. 技术人员可以通过调整状态来覆盖自动判断
2. 系统保留原始日志供人工复查
3. 可以通过扩展分类关键词来修正分类错误
4. 可以通过调整权重来修正优先级错误
5. 长期方案：引入人工反馈闭环

## 规模扩展
如果日志量从 30 条变成 30 万条：
1. 存储：SQLite → PostgreSQL/ClickHouse
2. 解析：单文件读取 → 流式 Kafka 消费
3. 指纹计算：需要时间索引和 fingerprint 索引
4. 合并算法：需要增量更新和滑动窗口
5. 前端：需要分页、搜索和过滤
6. 配置：规则配置化和版本管理
7. 可观测性：添加指标和监控
```

---

## ✅ 第十八步：最终验收检查

### 功能检查
- [ ] `uvicorn app.main:app` 能启动
- [ ] 访问 http://localhost:8000 能看到页面
- [ ] 点击异常列表能看到数据
- [ ] 统计信息数字正确
- [ ] 更新状态功能正常

### 测试检查
- [ ] `pytest tests/ -v` 全部通过
- [ ] 测试覆盖核心逻辑（不是只测页面）
- [ ] 边界情况有测试

### 数据检查
- [ ] 至少 30 条日志（建议 50 条）
- [ ] 包含 5+ 个不同来源
- [ ] 包含脏数据（无效 JSON）
- [ ] 包含中英文混杂
- [ ] 包含同一根因不同表述
- [ ] 包含动态内容（订单号等）

### 文档检查
- [ ] README.md 包含所有必需章节
- [ ] AI_USAGE.md 包含 AI 错误和纠正
- [ ] DECISIONS.md 包含关键决策说明

### 核心逻辑检查
- [ ] 分类不是写死的（基于规则匹配）
- [ ] 合并有 fingerprint 算法
- [ ] 优先级有评分公式
- [ ] 人工介入有判断规则
- [ ] 每个决策都能追溯到规则

---

## ⏱️ 时间预算

| 步骤 | 内容 | 时间 |
|------|------|------|
| 1-2 | 项目结构 + 依赖 | 10 分钟 |
| 3-5 | models + parser + normalizer | 45 分钟 |
| 6-9 | classifier + deduplicator + prioritizer + recommender | 1.5 小时 |
| 10-11 | storage + main + HTML | 45 分钟 |
| 12 | 构造模拟数据（50条） | 1 小时 |
| 13 | 编写测试 | 45 分钟 |
| 14-17 | 四个文档 | 1 小时 |
| 18 | 验收调试 | 15 分钟 |
| **总计** | | **约 6 小时** |

---

## 💡 额外提示

1. **先实现核心逻辑，再写前端** - 前端挂上就行，核心逻辑才是评估重点
2. **用 AI 加速但不要盲从** - 每次 AI 生成代码后都要看一遍、测试一下
3. **记录 AI 的错误** - 面试官非常看重这个
4. **测试测边界** - 不只是 happy path
5. **文档写自己的思考** - 不要写流水账
6. **数据质量高于数量** - 50 条有深度的数据比 100 条重复数据好
