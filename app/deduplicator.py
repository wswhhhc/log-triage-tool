import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime
from typing import Dict, List

from app.models import AnomalyIssue, AnomalyType, LogEntry

# 需要去除的动态模式
DYNAMIC_PATTERNS = [
    (re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I), '<UUID>'),
    (re.compile(r'\{?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\}?', re.I), '<UUID>'),
    (re.compile(r'\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?'), '<DATETIME>'),
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
    # URL / URI
    (re.compile(r'https?://\S+'), '<URL>'),
    (re.compile(r'/data/\S+'), '<PATH>'),
    # 容器 / Pod / 主机名
    (re.compile(r'pod[_-]?\w+', re.I), '<POD>'),
    (re.compile(r'container[_-]?\w+', re.I), '<CONTAINER>'),
    # 随机哈希（如 git commit, image tag）
    (re.compile(r'\b[0-9a-f]{7,40}\b', re.I), '<HASH>'),
    # 端口号（跟在 : 后面的数字）
    (re.compile(r':\d{2,5}\b'), ':<PORT>'),
]

# 同义词归一化模式
SYNONYM_PATTERNS = [
    (re.compile(r'\b(?:timeout|timed out|timed-out)\b|超时', re.I), 'TIMEOUT'),
    (re.compile(r'\b(?:database|db|mysql|postgres|redis)\b|数据库', re.I), 'DATABASE'),
    (re.compile(r'\b(?:failed|failure)\b|失败', re.I), 'FAILED'),
    (re.compile(r'\b(?:permission denied|unauthorized|forbidden|auth)\b|无权限|未授权|认证', re.I), 'AUTH'),
    (re.compile(r'\b(?:connection refused|network|dns|socket)\b|连接被拒绝|网络', re.I), 'NETWORK'),
    (re.compile(r'\b(?:rate limit|throttle|quota)\b|限流|配额', re.I), 'RESOURCE_LIMIT'),
    # 常见缩写归一化
    (re.compile(r'\bconn\b', re.I), 'connection'),
    (re.compile(r'\berr\b', re.I), 'error'),
    (re.compile(r'\bwarn\b', re.I), 'warning'),
    (re.compile(r'\binfo\b', re.I), 'information'),
    (re.compile(r'\breq\b', re.I), 'request'),
    (re.compile(r'\bresp\b', re.I), 'response'),
    (re.compile(r'\baddr\b', re.I), 'address'),
    (re.compile(r'\bconfig\b', re.I), 'configuration'),
]


# 停用词 — 归一化后过滤掉，不参与指纹
STOP_WORDS = {
    # English
    "after", "for", "the", "at", "with", "to", "of", "in", "on", "a", "an",
    "is", "was", "by", "from", "and", "or", "be", "it", "as",
    "are", "were", "has", "have", "had", "been", "being",
    "this", "that", "these", "those",
    "not", "no", "but", "if", "so",
    # Chinese （通过标点分割后成为独立 token 的高频虚词）
    "的", "了", "在", "是", "和", "就", "也",
    "这", "那", "之", "为", "对", "从", "与",
    "其", "中", "能", "下", "上", "时", "后",
    "等", "及", "但", "而", "或", "如", "因",
}


def normalize_message(message: str) -> str:
    """去除动态内容并归一化同义词，生成标准化模板"""
    if not isinstance(message, str):
        message = str(message) if message else ""
    result = message.lower()
    # 替换中英文标点为空格
    for ch in ":：，。()（）,;；？！""''【】《》——…·、":
        result = result.replace(ch, " ")

    # 中文同义短句归一化（在动态内容替换前）
    result = re.sub(r'连接池已满|连接池耗尽|connection pool exhausted|connection pool is full', ' pool_exhausted ', result)
    result = re.sub(r'无响应|no response|no respon', ' noresponse ', result)
    result = re.sub(r'查询失败|query failed', ' query_failed ', result)

    # 1. 去除动态内容
    for pattern, replacement in DYNAMIC_PATTERNS:
        result = pattern.sub(replacement, result)

    # 2. 同义词归一化
    for pattern, replacement in SYNONYM_PATTERNS:
        result = pattern.sub(f' {replacement.lower()} ', result)

    # 额外规范化
    result = re.sub(r'\b(?:connection\s+)?timeout\b', ' timeout ', result)
    result = re.sub(r'\bconnection timeout\b', ' timeout ', result)
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


def _make_content_signature(raw: dict) -> str:
    """从原始日志生成短签名，用于 unknown source 消歧"""
    try:
        keys_to_hash = {k: raw[k] for k in ("service", "app", "system", "source",
                                              "job_name", "job", "task", "host") if k in raw}
        if not keys_to_hash:
            # 没有可识别字段 → 用原始内容的 hash 前缀
            return hashlib.md5(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()[:8]
        raw_sig = json.dumps(keys_to_hash, sort_keys=True, default=str)
        return hashlib.md5(raw_sig.encode()).hexdigest()[:8]
    except (TypeError, ValueError):
        return "unknown"


def generate_fingerprint(log: LogEntry, anomaly_type: AnomalyType) -> str:
    """
    生成异常指纹
    指纹组成：来源系统 + 异常类型 + 标准化消息模板

    当 source 为 "unknown" 时，引入原始内容的签名来消歧，
    避免不同服务的脏数据被错误归为同一 issue。
    """
    normalized_msg = normalize_message(log.message)
    source = log.source

    # source 为 unknown 时用签名消歧
    if source == "unknown" and log.raw:
        sig = _make_content_signature(log.raw)
        source = f"unknown_{sig}"

    fingerprint_content = f"{source}|{anomaly_type.value}|{normalized_msg}"
    return hashlib.md5(fingerprint_content.encode()).hexdigest()


def merge_anomalies(anomaly_logs: List[LogEntry],
                    classified: Dict[str, AnomalyType]) -> List[AnomalyIssue]:
    """将异常日志按 fingerprint 合并"""
    groups = defaultdict(list)
    for log in anomaly_logs:
        # 防御：如果分类不存在，回退为 UNKNOWN
        atype = classified.get(log.id, AnomalyType.UNKNOWN)
        fp = generate_fingerprint(log, atype)
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
