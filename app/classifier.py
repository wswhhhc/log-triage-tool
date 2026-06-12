import re

from app.models import LogEntry, LogLevel, AnomalyType

# 错误等级集合
ERROR_LEVELS = {LogLevel.ERROR, LogLevel.FATAL, LogLevel.CRITICAL}

# 异常关键词（中文+英文）
# 注意：匹配会自动转小写，此处大小写仅为可读性
ANOMALY_KEYWORDS = {
    "timeout", "超时", "timed out", "timed-out",
    "exception", "异常",
    "failed", "失败", "failure",
    "denied", "拒绝", "rejected",
    "unavailable", "不可用",
    "connection refused", "连接被拒绝",
    "aborted", "中止",
}

# 需要精确匹配（单词边界）的关键词
ANOMALY_KEYWORD_EXACT = {
    "error",  # 避免匹配 "no error"、"errorless"
}


def _has_word(text: str, word: str) -> bool:
    """单词边界匹配"""
    return bool(re.search(rf'\b{re.escape(word)}\b', text, re.IGNORECASE))


def is_anomaly(log: LogEntry) -> bool:
    """判断日志是否异常"""
    if log.level in ERROR_LEVELS:
        return True

    msg_lower = log.message.lower()
    for kw in ANOMALY_KEYWORDS:
        if kw.lower() in msg_lower:
            return True
    for kw in ANOMALY_KEYWORD_EXACT:
        if _has_word(msg_lower, kw.lower()):
            return True

    if log.is_dirty and log.dirty_reason:
        return True

    return False


def classify(log: LogEntry) -> AnomalyType:
    """对异常日志进行分类（注意：TIMEOUT 优先级最高，必须放最前）"""
    msg = log.message.lower() if log.message else ""

    # ── TIMEOUT（放最前面，因为 timeout 常与其他类型共存） ──
    if any(kw in msg for kw in ["timeout", "超时", "timed out", "timed-out"]):
        return AnomalyType.TIMEOUT

    # ── AUTH ──
    if any(kw in msg for kw in [
        "auth", "认证", "token", "permission", "权限",
        "unauthorized", "未授权", "denied", "forbidden",
    ]):
        return AnomalyType.AUTH

    # ── DATABASE ──
    if any(kw in msg for kw in [
        "database", "数据库", "db", "sql", "mysql", "postgres",
        "mongo", "redis", "连接池", "connection pool",
    ]):
        return AnomalyType.DATABASE

    # ── NETWORK ──
    if any(kw in msg for kw in [
        "network", "网络", "connection refused", "连接失败",
        "dns", "tcp", "socket", "host", "端口",
    ]):
        return AnomalyType.NETWORK

    # ── VALIDATION ──
    if any(kw in msg for kw in [
        "validation", "校验", "invalid", "无效",
        "param", "参数", "required", "必填", "format", "格式",
    ]):
        return AnomalyType.VALIDATION

    # ── RESOURCE_LIMIT ──
    if any(kw in msg for kw in [
        "limit", "限流", "throttle", "quota", "容量",
        "overload", "过载", "queue", "积压", "full", "已满",
        "disk quota", "quota exceeded",
    ]):
        return AnomalyType.RESOURCE_LIMIT

    # ── 脏数据（仅在无法归类到其他类型时才标记为数据质量问题） ──
    if log.is_dirty:
        return AnomalyType.DATA_QUALITY

    return AnomalyType.UNKNOWN
