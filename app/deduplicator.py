import hashlib
import re
from collections import defaultdict
from datetime import datetime
from typing import Dict, List

from app.models import AnomalyIssue, AnomalyType, LogEntry

# 需要去除的动态模式
DYNAMIC_PATTERNS = [
    (re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I), '<UUID>'),
    (re.compile(r'\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?'), '<DATETIME>'),
    (re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b'), '<IP>'),
    (re.compile(r'\border[_-]?id[=:]\s*\S+', re.I), '<ORDER_ID>'),
    (re.compile(r'\b(?:ORD|TX|TXN|SKU)[_-]?\d+\b', re.I), '<ORDER_ID>'),
    (re.compile(r'\border[=:]\s*\d+\b', re.I), '<ORDER_ID>'),
    (re.compile(r'user[_-]?\d+', re.I), '<USER_ID>'),
    (re.compile(r'\bbatch[_\s]\d+\b', re.I), '<BATCH_ID>'),
    (re.compile(r'\bbatch\s+\d+\b', re.I), '<BATCH_ID>'),
    (re.compile(r'\b\d{6,}\b'), '<NUM_ID>'),
    (re.compile(r'(?<==)\d+(?:\s*ms)?\b'), '<NUM>'),
    (re.compile(r'\b\d+(?:\s*ms)\b'), '<DURATION>'),
    (re.compile(r'\bretry\s+\d+/\d+\b', re.I), 'retry <N>/<N>'),
    (re.compile(r'trace[_-]?id[=:]\s*\S+', re.I), 'trace_id=<TRACE_ID>'),
    (re.compile(r'\b\d+\.?\d*\s*(?:元|USD|CNY|dollars?)\b', re.I), '<AMOUNT>'),
    (re.compile(r'/\d+/'), '/<ID>/'),
]

# 同义词归一化模式
SYNONYM_PATTERNS = [
    (re.compile(r'\b(?:timeout|timed out|timed-out)\b|超时', re.I), 'TIMEOUT'),
    (re.compile(r'\b(?:database|db|mysql|postgres|redis)\b|数据库', re.I), 'DATABASE'),
    (re.compile(r'\b(?:failed|failure)\b|失败', re.I), 'FAILED'),
    (re.compile(r'\b(?:permission denied|unauthorized|forbidden|auth)\b|无权限|未授权|认证', re.I), 'AUTH'),
    (re.compile(r'\b(?:connection refused|network|dns|socket)\b|连接被拒绝|网络', re.I), 'NETWORK'),
    (re.compile(r'\b(?:rate limit|throttle|quota)\b|限流|配额', re.I), 'RESOURCE_LIMIT'),
]


# 停用词 — 归一化后过滤掉，不参与指纹
STOP_WORDS = {
    "after", "for", "the", "at", "with", "to", "of", "in", "on", "a", "an",
    "is", "was", "by", "from", "and", "or", "be", "it", "as",
}


def normalize_message(message: str) -> str:
    """去除动态内容并归一化同义词，生成标准化模板"""
    result = message.lower()
    # 替换中文标点为空格
    for ch in ":：，。()（）,":
        result = result.replace(ch, " ")

    # 中文同义短句归一化（在动态内容替换前，针对特定表述方式）
    result = re.sub(r'连接池已满|连接池耗尽|connection pool exhausted|connection pool is full', ' pool_exhausted ', result)
    result = re.sub(r'无响应|no response|no respon', ' noresponse ', result)
    result = re.sub(r'查询失败|query failed', ' query_failed ', result)

    # 1. 去除动态内容
    for pattern, replacement in DYNAMIC_PATTERNS:
        result = pattern.sub(replacement, result)

    # 2. 同义词归一化
    for pattern, replacement in SYNONYM_PATTERNS:
        result = pattern.sub(f' {replacement.lower()} ', result)

    # 额外：connection timeout → timeout, database connection timeout → database timeout
    result = re.sub(r'\b(?:connection\s+)?timeout\b', ' timeout ', result)
    # 额外：db connection timeout → timeout（db 已被归一化为 database，剩下 connection timeout）
    result = re.sub(r'\bconnection timeout\b', ' timeout ', result)
    # 去除 DURATION token（不影响根因判断）
    result = re.sub(r'<DURATION>', ' ', result)

    # 3. 分词、去停用词、去重
    tokens = result.split()
    seen = set()
    deduped = []
    for token in tokens:
        if token in STOP_WORDS:
            continue
        if token not in seen:
            seen.add(token)
            deduped.append(token)
    return ' '.join(deduped)


def generate_fingerprint(log: LogEntry, anomaly_type: AnomalyType) -> str:
    """
    生成异常指纹
    指纹组成：来源系统 + 异常类型 + 标准化消息模板
    """
    normalized_msg = normalize_message(log.message)
    fingerprint_content = f"{log.source}|{anomaly_type.value}|{normalized_msg}"
    return hashlib.md5(fingerprint_content.encode()).hexdigest()


def merge_anomalies(anomaly_logs: List[LogEntry],
                    classified: Dict[str, AnomalyType]) -> List[AnomalyIssue]:
    """将异常日志按 fingerprint 合并"""
    groups = defaultdict(list)
    for log in anomaly_logs:
        fp = generate_fingerprint(log, classified[log.id])
        groups[fp].append(log)

    issues = []
    for i, (fp, logs) in enumerate(groups.items()):
        sorted_logs = sorted(logs, key=lambda l: l.timestamp or datetime.min)

        type_counts: Dict[AnomalyType, int] = defaultdict(int)
        for log in logs:
            type_counts[classified[log.id]] += 1
        dominant_type = max(type_counts, key=type_counts.get)

        timestamps = [l.timestamp for l in sorted_logs if l.timestamp]
        first_seen = timestamps[0] if timestamps else datetime.now()
        last_seen = timestamps[-1] if timestamps else datetime.now()

        issues.append(AnomalyIssue(
            id=f"issue_{i}",
            fingerprint=fp,
            anomaly_type=dominant_type,
            priority=None,
            source=logs[0].source,
            message_template=normalize_message(logs[0].message),
            related_log_ids=[log.id for log in logs],
            first_seen=first_seen,
            last_seen=last_seen,
            occurrence_count=len(logs),
        ))

    return issues
